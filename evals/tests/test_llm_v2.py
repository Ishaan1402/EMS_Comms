"""No network: transport responses are artificial software fixtures."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.contracts import CASE_FIELDS, ROOT, load_cases, load_json
from evals.llm_v2 import (DEFAULT_CONFIG, attention_errors, attention_schema_v2,
                         awareness_schema_v2, build_v2_plan, convert_outputs,
                         empty_references, execute_v2, report_batch)
from evals.providers import ProviderError, generate
from evals.v2 import VERSIONS

OUTPUT = {"summary": "Artificial output: not a clinical judgment.", "meaningful_change": "Unclear",
          "change_direction": None, "evidence_missing_information": "Artificial fixture only.",
          "attention_category": "Cannot assess", "attention_reason": "Artificial fixture only."}
ENV = {"OPENAI_API_KEY": "test-only", "ANTHROPIC_API_KEY": "test-only", "GEMINI_API_KEY": "test-only"}


class V2LLMTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_cases()
        self.spec = load_json(DEFAULT_CONFIG)
        self.spec["case_ids"] = ["SYN001", "SYN002"]
        self.spec["queue_ids"] = []
        self.plan, self.jobs, self.selected, self.groups = build_v2_plan(self.spec, self.cases, model_ids=["qwen9b"], environ={})

    def fake(self, model, system, user, schema, limit, timeout, **kwargs):
        output = ({"order": {cid: "Cannot assess" for cid in schema["properties"]["order"]["properties"]},
                   "explanation": "Artificial test, not a clinical judgment."} if "order" in schema["properties"] else copy.deepcopy(OUTPUT))
        return {"status": "ok", "output": output, "raw_text": json.dumps(output),
                "input_tokens": 10, "output_tokens": 20, "actual_model": "artificial-test-version"}

    def run_fake(self, out, **kwargs):
        return execute_v2(self.plan, self.jobs, self.selected, self.groups, out,
                          consent=True, synthetic=True, max_usd=0, generator=self.fake, environ={}, **kwargs)

    def test_plan_has_v2_schemas_and_no_network(self):
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            plan, jobs, _, _ = build_v2_plan(self.spec, self.cases, model_ids=["openai_luna"], environ={})
        self.assertEqual(plan["kind"], "experiment_plan_v2")
        self.assertEqual(plan["planned_requests"], 2)
        self.assertEqual(plan["contract_versions"], VERSIONS)
        self.assertEqual(plan["configs"][0]["input_contract_version"], "clinician_form_v2")
        self.assertTrue(all(j["schema"] == awareness_schema_v2() for j in jobs))
        self.assertFalse(plan["key_presence"]["openai"])

    def test_source_whitelist_and_no_reference_leakage(self):
        for c in self.cases:
            c.update({"reference_summary": "SECRET ANSWER", "reviewer_id": "SECRET REVIEWER"})
        plan, jobs, _, _ = build_v2_plan(self.spec, self.cases, model_ids=["qwen9b"], environ={})
        serialized = json.dumps(plan) + json.dumps(jobs)
        self.assertNotIn("SECRET ANSWER", serialized)
        self.assertNotIn("SECRET REVIEWER", serialized)
        self.assertTrue(all(set(j["input"]) == set(CASE_FIELDS) for j in jobs))

    def test_v1_prompts_or_dataset_cannot_be_used(self):
        for field, value in (("prompt_versions", ["awareness_v2"]), ("queue_prompt_version", "queue_v1"),
                             ("dataset_version", "new-unreviewed-dataset")):
            spec = copy.deepcopy(self.spec)
            spec[field] = value
            with self.assertRaises(ValueError):
                build_v2_plan(spec, self.cases)

    def test_missing_gates_and_keys_never_call_or_create_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            for options in ({}, {"consent": True}, {"consent": True, "synthetic": True, "max_usd": float("inf")},
                            {"consent": True, "synthetic": True, "max_usd": 0, "max_requests": 1}):
                with self.assertRaises(ValueError):
                    execute_v2(self.plan, self.jobs, self.selected, self.groups, out, generator=lambda *a, **k: self.fail("Generator called"), **options)
                self.assertFalse(out.exists())
            plan, jobs, cases, groups = build_v2_plan(self.spec, self.cases, model_ids=["openai_luna"], environ={})
            for budget, pricing in ((0, False), (.00000001, True), (1, False), (1, True)):
                with self.assertRaises(ValueError):
                    execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True, max_usd=budget,
                               confirm_pricing=pricing, environ={}, generator=lambda *a, **k: self.fail("Generator called"))
                self.assertFalse(out.exists())

    def test_incompatible_references_rejected_before_call(self):
        refs = {"kind": "clinician_reference"}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with self.assertRaisesRegex(ValueError, "References must match"):
                self.run_fake(out, references=refs)
            self.assertFalse(out.exists())

    def test_unknown_reference_not_hidden_by_subset_filter(self):
        refs = empty_references(self.cases)
        refs["cases"].append({"case_id": "UNKNOWN"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with self.assertRaisesRegex(ValueError, "reference ID"):
                self.run_fake(out, references=refs)
            self.assertFalse(out.exists())

    def test_commercial_timeout_cost_stays_unknown(self):
        plan, jobs, cases, groups = build_v2_plan(self.spec, self.cases, model_ids=["openai_luna"], environ={})
        def fail(*args, **kwargs):
            raise ProviderError("request_timeout", status="timeout")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            batch = execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True, max_usd=1,
                               confirm_pricing=True, generator=fail, environ=ENV)
            self.assertTrue(all(r["cost_usd"] is None for r in batch["records"]))
            report = load_json(out / "report/openai_luna.awareness_form_v2.repeat-1/metrics.json")
            self.assertIsNone(report["reported_case_cost_usd"])
            self.assertIsNone(report["schema_validation_rate"])
            self.assertEqual(report["error_rate"], 1)
            self.assertEqual(load_json(out / "run_summary.json")["unknown_cost_attempts"], 2)

    def test_success_collects_v2_outputs_and_reports_but_no_quality_scores(self):
        refs = empty_references(self.cases)
        before = copy.deepcopy(refs)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
                batch = self.run_fake(out, references=refs)
            self.assertEqual(batch["kind"], "model_predictions_v2_batch")
            self.assertEqual(len(batch["records"]), 2)
            self.assertEqual(len(batch["exercises"]), 0)
            metric = load_json(out / "report/qwen9b.awareness_form_v2.repeat-1/metrics.json")
            self.assertEqual(metric["cases_total"], 2)
            self.assertEqual(metric["approved_reference_cases"], 0)
            self.assertIsNone(metric["attention"]["accuracy"])
            self.assertEqual(metric["usable_response_rate"], 1)
            self.assertEqual(metric["exercises"], [])
            self.assertGreaterEqual(metric["latency_median_ms"], 0)
            self.assertEqual(metric["reported_case_cost_usd"], 0)
            self.assertEqual(metric["case_cost_coverage"], 1)
            self.assertEqual(load_json(out / "run_summary.json")["attempts"], 2)
            self.assertEqual(refs, before)
            with self.assertRaises(FileExistsError):
                self.run_fake(out)

    def test_malformed_outputs_not_repaired_or_retried(self):
        outputs = ["not JSON", {**OUTPUT, "preparation_category": "Prepare now"},
                   {**OUTPUT, "change_direction": "Worsened"}, {**OUTPUT, "summary": ""}]
        for index, output in enumerate(outputs):
            def malformed(*args, **kwargs):
                result = self.fake(*args, **kwargs)
                result["output"] = output
                return result
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "run"
                batch = execute_v2(self.plan, self.jobs, self.selected, self.groups, out, consent=True, synthetic=True,
                                   max_usd=0, generator=malformed, environ={})
                self.assertTrue(all(r["status"] == "error" and r["schema_errors"] for r in batch["records"]), index)
                self.assertTrue(all(r["output"] == output for r in batch["records"]))
                self.assertEqual(load_json(out / "run_summary.json")["attempts"], 2)

    def test_timeout_refusal_and_http_retry_behavior(self):
        for mode in ("timeout", "refusal", "retry"):
            spec = copy.deepcopy(self.spec)
            spec["max_retries"] = 1
            plan, jobs, cases, groups = build_v2_plan(spec, self.cases, model_ids=["qwen9b"], environ={})
            calls = []
            def response(*args, **kwargs):
                calls.append(1)
                if mode == "timeout":
                    raise ProviderError("request_timeout", status="timeout")
                if mode == "refusal":
                    return {"status": "refusal", "output": None}
                if len(calls) % 2:
                    raise ProviderError("http_429", retryable=True)
                return self.fake(*args, **kwargs)
            with tempfile.TemporaryDirectory() as tmp:
                result = execute_v2(plan, jobs, cases, groups, Path(tmp) / "run", consent=True, synthetic=True,
                                    max_usd=0, generator=response, environ={}, sleep=lambda _: None)
                self.assertEqual(len(calls), 4 if mode == "retry" else 2)
                self.assertTrue(all(r["retry_count"] == (1 if mode == "retry" else 0) for r in result["records"]))

    def test_attention_contract_no_forced_rank_or_category_crosswalk(self):
        schema = attention_schema_v2(["SYN001", "SYN004", "SYN007"])
        self.assertEqual(schema["properties"]["order"]["required"], ["SYN001", "SYN004", "SYN007"])
        self.assertIn("Cannot assess", json.dumps(schema))
        good = {"order": {"SYN001": 1, "SYN004": 1, "SYN007": "Cannot assess"}, "explanation": "Artificial test"}
        self.assertEqual(attention_errors(good, list(good["order"])), [])
        for val in (True, "Prepare now", "Unsure", 4):
            bad = copy.deepcopy(good)
            bad["order"]["SYN001"] = val
            self.assertTrue(attention_errors(bad, list(good["order"])))

    def test_all_providers_through_actual_payload_builder_and_parser_offline(self):
        spec = copy.deepcopy(self.spec)
        spec.pop("case_ids")
        spec["queue_ids"] = ["A01", "A02"]
        spec["repeats"] = 2
        plan, jobs, cases, groups = build_v2_plan(spec, self.cases,
            model_ids=["openai_luna", "claude_haiku", "gemini_lite", "qwen9b"], environ={})
        calls = []
        def via_provider(model, system, user, schema, limit, timeout, **kwargs):
            output = self.fake(model, system, user, schema, limit, timeout)["output"]
            def transport(url, headers, payload, timeout):
                calls.append((model["provider"], payload))
                encoded = json.dumps(output)
                if model["provider"] == "openai":
                    self.assertEqual(payload["text"]["format"]["schema"], schema)
                    return {"status": "completed", "model": "fixture", "output": [{"content": [{"type": "output_text", "text": encoded}]}],
                            "usage": {"input_tokens": 10, "output_tokens": 20}}
                if model["provider"] == "anthropic":
                    self.assertEqual(payload["output_config"]["format"]["schema"], schema)
                    return {"stop_reason": "end_turn", "model": "fixture", "content": [{"type": "text", "text": encoded}],
                            "usage": {"input_tokens": 10, "output_tokens": 20}}
                if model["provider"] == "gemini":
                    self.assertEqual(payload["generationConfig"]["responseFormat"]["text"]["schema"], schema)
                    return {"modelVersion": "fixture", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": encoded}]}}],
                            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}}
                self.assertEqual(payload["format"], schema)
                return {"done": True, "done_reason": "stop", "model": "fixture", "message": {"content": encoded},
                        "prompt_eval_count": 10, "eval_count": 20}
            return generate(model, system, user, schema, limit, timeout, transport=transport, environ=ENV)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
                batch = execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True, max_usd=10,
                                   max_requests=100, confirm_pricing=True, generator=via_provider, environ=ENV)
            self.assertEqual(len(calls), 96)
            self.assertEqual(len(batch["records"]), 80)
            self.assertEqual(len(batch["exercises"]), 16)
            self.assertTrue(all(r["status"] == "ok" for r in batch["records"] + batch["exercises"]))
            self.assertEqual(len(list((out / "report").glob("*/metrics.json"))), 8)
            self.assertNotIn("test-only", (out / "plan.json").read_text())
            score = subprocess.run([sys.executable, "-m", "evals.llm_v2", "score", "--run", str(out),
                                    "--out", str(Path(tmp) / "rescored")], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(score.returncode, 0, score.stderr)
            self.assertEqual(load_json(out / "report/index.json"), load_json(Path(tmp) / "rescored/index.json"))

    def test_interruption_saves_v2_partial_outputs_and_missing_denominators(self):
        calls = []
        def stop(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return self.fake(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "partial"
            with self.assertRaises(KeyboardInterrupt):
                execute_v2(self.plan, self.jobs, self.selected, self.groups, out, consent=True, synthetic=True,
                           max_usd=0, generator=stop, environ={})
            batch = load_json(out / "predictions_v2.json")
            self.assertEqual(len(batch["records"]), 1)
            metric = load_json(out / "report/qwen9b.awareness_form_v2.repeat-1/metrics.json")
            self.assertEqual(metric["cases_total"], 2)
            self.assertEqual(metric["usable_response_rate"], .5)

    def test_duplicate_unknown_batch_record_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            batch = self.run_fake(out)
            batch["records"].append(copy.deepcopy(batch["records"][0]))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                report_batch(self.plan, self.selected, self.groups, batch, Path(tmp) / "bad")
            batch["records"].pop()
            batch["records"][0]["config_id"] = "unknown"
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                report_batch(self.plan, self.selected, self.groups, batch, Path(tmp) / "bad")

    def test_cli_without_execute_is_plan_only_even_with_run_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan"
            result = subprocess.run([sys.executable, "-m", "evals.llm_v2", "run", "--models", "qwen9b", "--out", str(out)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PLAN ONLY", result.stdout)
            self.assertEqual({p.name for p in out.iterdir()}, {"plan.json"})
            self.assertEqual(load_json(out / "plan.json")["planned_requests"], 2)
            missing = subprocess.run([sys.executable, "-m", "evals.llm_v2", "run", "--execute", "--models", "qwen9b",
                                      "--out", str(Path(tmp) / "blocked")], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(missing.returncode, 2)
            self.assertFalse((Path(tmp) / "blocked").exists())


if __name__ == "__main__":
    unittest.main()
