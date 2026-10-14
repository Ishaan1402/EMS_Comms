import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.contracts import ROOT, load_cases, load_json
from evals.experiments import build_plan, case_input, execute_plan, score_experiment
from evals.providers import (ProviderError, awareness_schema, generate, headers_for,
                            parse_response, post_json, queue_schema, request_spec)


OUTPUT = {"summary": "FICTIONAL TEST OUTPUT, not a medical judgment.", "meaningful_change": "Unclear",
          "change_explanation": "Test fixture only.", "missing_information": "Not clinically assessed.",
          "preparation_category": "Unsure"}


class ProviderTests(unittest.TestCase):
    def model(self, provider):
        return {"id": "test", "provider": provider, "model": "test-model"}

    def test_openai_strict_schema_and_no_storage(self):
        url, body = request_spec(self.model("openai"), "system", "input", awareness_schema(), 1024)
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertIs(body["store"], False)
        self.assertIs(body["text"]["format"]["strict"], True)
        self.assertEqual(body["text"]["format"]["schema"], awareness_schema())

    def test_anthropic_schema(self):
        _, body = request_spec(self.model("anthropic"), "system", "input", awareness_schema(), 1024)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(body["max_tokens"], 1024)

    def test_gemini_schema_and_headers_not_query(self):
        url, body = request_spec(self.model("gemini"), "system", "input", awareness_schema(), 1024)
        self.assertNotIn("?", url)
        self.assertEqual(body["generationConfig"]["responseFormat"]["text"]["mimeType"], "APPLICATION_JSON")
        self.assertEqual(headers_for("gemini", {"GEMINI_API_KEY": "test-secret"}), {"x-goog-api-key": "test-secret"})

    def test_ollama_no_stream_schema_and_local_only(self):
        _, body = request_spec(self.model("ollama"), "system", "input", awareness_schema(), 1024)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["format"], awareness_schema())
        for base in ("https://127.0.0.1", "http://example.com", "http://user:pass@localhost", "http://localhost/path", "http://localhost?token=x"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                request_spec({**self.model("ollama"), "base_url": base}, "", "", awareness_schema(), 1024)

    def test_key_errors_do_not_expose_keys(self):
        with self.assertRaisesRegex(ValueError, "Missing OPENAI_API_KEY"):
            headers_for("openai", {})
        with self.assertRaises(ValueError) as context:
            headers_for("openai", {"OPENAI_API_KEY": "secret\ninvalid"})
        self.assertNotIn("secret", str(context.exception))

    def test_openai_parse_and_refusal(self):
        raw = {"status": "completed", "model": "actual-snapshot", "usage": {"input_tokens": 20, "output_tokens": 30},
               "output": [{"content": [{"type": "output_text", "text": json.dumps(OUTPUT)}]}]}
        result = parse_response("openai", raw)
        self.assertEqual(result["output"], OUTPUT)
        self.assertEqual(result["actual_model"], "actual-snapshot")
        raw["output"][0]["content"] = [{"type": "refusal", "refusal": "Declined"}]
        self.assertEqual(parse_response("openai", raw)["status"], "refusal")

    def test_anthropic_truncation_and_refusal(self):
        raw = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": json.dumps(OUTPUT)}]}
        self.assertEqual(parse_response("anthropic", raw)["status"], "error")
        raw["stop_reason"] = "refusal"
        self.assertEqual(parse_response("anthropic", raw)["status"], "refusal")

    def test_gemini_bills_thinking_tokens_without_exposing_thoughts(self):
        raw = {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "thoughtsTokenCount": 30},
               "candidates": [{"finishReason": "STOP", "content": {"parts": [
                   {"thought": True, "text": "private reasoning"}, {"text": json.dumps(OUTPUT)}]}}]}
        result = parse_response("gemini", raw)
        self.assertEqual(result["output_tokens"], 50)
        self.assertNotIn("private reasoning", result["raw_text"])
        raw["candidates"] = []
        raw["promptFeedback"] = {"blockReason": "SAFETY"}
        self.assertEqual(parse_response("gemini", raw)["status"], "refusal")

    def test_ollama_parse_and_truncated_json_rejected(self):
        raw = {"done": True, "done_reason": "stop", "message": {"content": json.dumps(OUTPUT)},
               "prompt_eval_count": 20, "eval_count": 30}
        self.assertEqual(parse_response("ollama", raw)["output"], OUTPUT)
        raw["done_reason"] = "length"
        self.assertEqual(parse_response("ollama", raw)["status"], "error")

    def test_json_is_not_repaired(self):
        for content in ('```json\n{}\n```', '{"a":1,"a":2}', '{"a":NaN}'):
            raw = {"done": True, "message": {"content": content}}
            self.assertEqual(parse_response("ollama", raw)["output"], content)

    def test_generate_uses_injected_transport_no_network(self):
        seen = []
        def fake(url, headers, payload, timeout):
            seen.append((url, headers, payload, timeout))
            return {"done": True, "message": {"content": json.dumps(OUTPUT)}}
        result = generate(self.model("ollama"), "system", "user", awareness_schema(), 1024, 45, transport=fake)
        self.assertEqual(result["output"], OUTPUT)
        self.assertEqual(len(seen), 1)

    def test_redirects_are_blocked(self):
        from evals.providers import NoRedirect
        with self.assertRaisesRegex(ProviderError, "redirect_blocked"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere")

    def test_http_errors_sanitized(self):
        from urllib.error import HTTPError
        fake = HTTPError("https://api.openai.com", 429, "SECRET ERROR BODY", {}, None)
        with patch("evals.providers.build_opener") as opener:
            opener.return_value.open.side_effect = fake
            with self.assertRaises(ProviderError) as error:
                post_json("https://api.openai.com", {}, {}, 1)
        self.assertEqual(error.exception.code, "http_429")
        self.assertTrue(error.exception.retryable)
        self.assertNotIn("SECRET", str(error.exception))


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.queues = load_cases(), load_json(ROOT / "evals/queues.json")
        self.spec = load_json(ROOT / "evals/experiments.json")
        self.spec["repeats"] = 1
        self.spec["prompt_versions"] = ["awareness_v1"]
        self.plan, self.jobs, self.selected, self.groups = build_plan(self.spec, self.cases, self.queues, model_ids=["qwen9b"], environ={})

    def fake(self, model, system, user, schema, max_output_tokens, timeout, **kwargs):
        output = dict(OUTPUT)
        if "order" in schema["properties"]:
            output = {"order": {cid: "Unsure" for cid in schema["properties"]["order"]["properties"]}, "explanation": "Fictional test only."}
        return {"status": "ok", "output": output, "raw_text": json.dumps(output),
                "input_tokens": 10, "output_tokens": 20, "actual_model": "FAKE_TEST_MODEL"}

    def run_fake(self, out, **kwargs):
        return execute_plan(self.plan, self.jobs, self.selected, self.groups, out, consent=True,
                            synthetic=True, max_usd=0, generator=self.fake, environ={}, sleep=lambda _: None, **kwargs)

    def test_plan_has_counts_and_no_transport_calls(self):
        self.assertEqual(self.plan["planned_requests"], 12)
        self.assertEqual(self.plan["conservative_reservation_usd"], 0)
        with patch("evals.providers.post_json", side_effect=AssertionError("Network forbidden")):
            plan, _, _, _ = build_plan(self.spec, self.cases, self.queues, environ={})
        self.assertEqual(len(plan["configs"]), 7)
        self.assertTrue(all(not present for present in plan["key_presence"].values()))

    def test_annotation_fields_cannot_leak(self):
        case = {**self.cases[0], "reference_summary": "SECRET GOLD", "preparation_category": "Prepare now"}
        self.assertNotIn("SECRET GOLD", json.dumps(case_input(case)))
        self.assertNotIn("preparation_category", case_input(case))

    def test_prompt_path_traversal_rejected(self):
        self.spec["prompt_versions"] = ["../../secret"]
        with self.assertRaises(ValueError):
            build_plan(self.spec, self.cases, self.queues)

    def test_credentials_cannot_be_in_config(self):
        self.spec["models"][0]["api_key"] = "do-not-store"
        with self.assertRaises(ValueError):
            build_plan(self.spec, self.cases, self.queues)

    def test_unknown_model_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(self.spec, self.cases, self.queues, model_ids=["missing"])

    def test_holdout_requires_tuning_manifest_and_rejects_encounter_overlap(self):
        self.spec["purpose"] = "holdout"
        with self.assertRaisesRegex(ValueError, "tuning_encounter_ids"):
            build_plan(self.spec, self.cases, self.queues)
        self.spec["tuning_encounter_ids"] = [self.cases[0]["encounter_id"]]
        with self.assertRaisesRegex(ValueError, "leakage"):
            build_plan(self.spec, self.cases, self.queues)
        self.spec["tuning_encounter_ids"] = ["OTHER_DEVELOPMENT_ENCOUNTER"]
        plan, _, _, _ = build_plan(self.spec, self.cases, self.queues)
        self.assertEqual(plan["purpose"], "holdout")

    def test_interruption_keeps_partial_artifacts(self):
        def stop(*args, **kwargs):
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "partial"
            with self.assertRaises(KeyboardInterrupt):
                execute_plan(self.plan, self.jobs, self.selected, self.groups, out,
                             consent=True, synthetic=True, max_usd=0, generator=stop, environ={})
            self.assertEqual(load_json(out / "predictions.json")["records"], [])
            summary = load_json(out / "run_summary.json")
            self.assertEqual(summary["unknown_cost_attempts"], 1)
            self.assertIn("interrupted", summary["stopped"])

    def test_subsets_do_not_leak_other_patients_into_queues(self):
        self.spec["case_ids"] = ["SYN001"]
        plan, jobs, _, queues = build_plan(self.spec, self.cases, self.queues, model_ids=["qwen9b"])
        self.assertEqual(plan["planned_requests"], 1)
        self.assertEqual(queues, [])
        self.spec["queue_ids"] = ["Q01"]
        with self.assertRaises(ValueError):
            build_plan(self.spec, self.cases, self.queues, model_ids=["qwen9b"])

    def test_execution_requires_all_gates_before_mkdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            for consent, synthetic, budget in ((False, True, 0), (True, False, 0), (True, True, float("inf"))):
                with self.assertRaises(ValueError):
                    execute_plan(self.plan, self.jobs, self.selected, self.groups, out,
                                 consent=consent, synthetic=synthetic, max_usd=budget, generator=self.fake)
                self.assertFalse(out.exists())
            with self.assertRaises(ValueError):
                self.run_fake(out, max_requests=1)
            self.assertFalse(out.exists())

    def test_commercial_budget_and_missing_keys_gate_before_requests(self):
        plan, jobs, cases, groups = build_plan(self.spec, self.cases, self.queues, model_ids=["openai_luna"], environ={})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            for budget, pricing in ((0, False), (0.000001, True), (100, True)):
                with self.assertRaises(ValueError):
                    execute_plan(plan, jobs, cases, groups, out, consent=True, synthetic=True, max_usd=budget,
                                 confirm_pricing=pricing, generator=self.fake, environ={})
                self.assertFalse(out.exists())

    def test_fake_run_saves_predictions_ledger_report_and_no_clinical_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            predictions = self.run_fake(out)
            self.assertEqual(len(predictions["records"]), 10)
            self.assertEqual(len(predictions["queues"]), 2)
            self.assertEqual(len((out / "attempts.jsonl").read_text().splitlines()), 12)
            summary = load_json(out / "run_summary.json")
            self.assertEqual(summary["known_estimated_token_cost_usd"], 0)
            report = score_experiment(self.plan, self.selected, self.groups, predictions, out / "report")
            self.assertEqual(report["approved_reference_cases"], 0)
            self.assertIsNone(report["results"][0]["preparation"]["accuracy"])
            with self.assertRaises(FileExistsError):
                self.run_fake(out)

    def test_failure_does_not_switch_provider_or_drop_case(self):
        def fail(*args, **kwargs):
            raise ProviderError("request_timeout", status="timeout")
        with tempfile.TemporaryDirectory() as tmp:
            predictions = execute_plan(self.plan, self.jobs, self.selected, self.groups, Path(tmp) / "run",
                                       consent=True, synthetic=True, max_usd=0, generator=fail, environ={})
        self.assertEqual(len(predictions["records"]), 10)
        self.assertTrue(all(r["status"] == "timeout" and r["retry_count"] == 0 for r in predictions["records"]))

    def test_retry_attempts_visible_and_latencies_count_waits(self):
        self.plan["spec"]["max_retries"] = 1
        self.plan["maximum_attempts"] = 24
        calls = []
        def retry(model, *args, **kwargs):
            calls.append(model["id"])
            if len(calls) % 2:
                raise ProviderError("http_429", retryable=True)
            return self.fake(model, *args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            predictions = execute_plan(self.plan, self.jobs, self.selected, self.groups, Path(tmp) / "run",
                                       consent=True, synthetic=True, max_usd=0, generator=retry, environ={}, sleep=lambda _: None)
        self.assertEqual(len(calls), 24)
        self.assertTrue(all(r["retry_count"] == 1 for r in predictions["records"]))
        self.assertTrue(all(r["input_tokens"] is None for r in predictions["records"]))


if __name__ == "__main__":
    unittest.main()
