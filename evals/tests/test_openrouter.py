"""Artificial transport fixtures only: never access a key or contact a model."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.contracts import ROOT, load_cases, load_json
from evals.llm_v2 import awareness_schema_v2, build_v2_plan, execute_v2
from evals.providers import ProviderError, generate, headers_for, parse_response, request_spec

CONFIG = ROOT / "evals/experiments_openrouter_v2.json"
ENV = {"OPENROUTER_API_KEY": "artificial-test-key"}
OUTPUT = {"summary": "Artificial fixture, not clinical evidence.", "meaningful_change": "Unclear",
          "change_direction": None, "evidence_missing_information": "Artificial fixture only.",
          "attention_category": "Cannot assess", "attention_reason": "Artificial fixture only."}


def response(content=None, **updates):
    return {"id": "gen-artificial", "provider": "Artificial serving provider",
            "model": "artificial/returned-version", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(OUTPUT) if content is None else content,
                        "reasoning": "DO NOT SAVE HIDDEN REASONING"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.00012}, **updates}


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_json(CONFIG)
        self.model = self.spec["models"][0]
        self.cases = load_cases()

    def plan(self, **changes):
        spec = {**copy.deepcopy(self.spec), **changes}
        return build_v2_plan(spec, self.cases, environ={})

    def fake(self, raw=None):
        def generator(model, system, user, schema, limit, timeout, **kwargs):
            return generate(model, system, user, schema, limit, timeout, environ=ENV,
                            transport=lambda *args: copy.deepcopy(response() if raw is None else raw))
        return generator

    def test_single_key_plan_is_offline_for_all_four_candidates(self):
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            plan, jobs, _, _ = self.plan()
        self.assertEqual(plan["planned_requests"], 8)
        self.assertEqual(plan["key_presence"], {"openrouter": False})
        self.assertEqual(len(plan["configs"]), 4)
        self.assertTrue(all(j["schema"] == awareness_schema_v2() for j in jobs))
        self.assertNotIn(ENV["OPENROUTER_API_KEY"], json.dumps(plan))

    def test_payload_pins_model_provider_and_schema_no_fallback_or_repair(self):
        url, payload = request_spec(self.model, "SYSTEM", "SOURCE", awareness_schema_v2(), 1024)
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(payload["model"], self.model["model"])
        self.assertEqual(payload["provider"], {"only": ["google-ai-studio"], "order": ["google-ai-studio"],
                         "allow_fallbacks": False, "require_parameters": True,
                         "max_price": {"prompt": .10, "completion": .40}})
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], awareness_schema_v2())
        self.assertEqual(payload["plugins"], [])
        self.assertEqual(payload["transforms"], [])
        self.assertNotIn("tools", payload)
        self.assertNotIn(ENV["OPENROUTER_API_KEY"], json.dumps(payload))
        self.assertEqual(headers_for("openrouter", ENV), {"Authorization": "Bearer artificial-test-key"})

    def test_invalid_or_ambiguous_settings_are_rejected(self):
        for changes in ({"openrouter_provider": None}, {"openrouter_provider": ["openai", "anthropic"]},
                        {"model": "openrouter/auto"}, {"model": "openai/model:free"},
                        {"model": "openai/model-latest"}, {"base_url": "https://untrusted.invalid"},
                        {"reasoning_effort": "low"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                request_spec({**self.model, **changes}, "", "", awareness_schema_v2(), 100)
        with self.assertRaises(ValueError):
            request_spec({**self.model, "provider": "openai"}, "", "", awareness_schema_v2(), 100)
        for key in ("api_key", "headers", "plugins", "provider_options"):
            spec = copy.deepcopy(self.spec)
            spec["models"][0][key] = "DO NOT SAVE"
            with self.assertRaisesRegex(ValueError, "Unknown model fields"):
                build_v2_plan(spec, self.cases, environ={})

    def test_missing_or_invalid_key_fails_without_exposing_secret(self):
        with self.assertRaisesRegex(ValueError, "Missing OPENROUTER_API_KEY"):
            headers_for("openrouter", {})
        with self.assertRaises(ValueError) as error:
            headers_for("openrouter", {"OPENROUTER_API_KEY": "secret\ninvalid"})
        self.assertNotIn("secret", str(error.exception))

    def test_parse_usage_and_identity_without_reasoning_or_error_metadata(self):
        result = parse_response("openrouter", response())
        self.assertEqual(result["output"], OUTPUT)
        self.assertEqual(result["reported_api_cost_usd"], .00012)
        self.assertEqual(result["input_tokens"], 100)
        self.assertEqual(result["output_tokens"], 50)
        self.assertEqual(result["actual_provider"], "Artificial serving provider")
        self.assertEqual(result["request_id"], "gen-artificial")
        self.assertNotIn("HIDDEN REASONING", json.dumps(result))

    def test_refusal_errors_truncation_and_malformed_json_remain_failures(self):
        for finish, status in (("length", "error"), ("content_filter", "refusal"), ("error", "error"),
                               ("tool_calls", "error")):
            raw = response()
            raw["choices"][0]["finish_reason"] = finish
            self.assertEqual(parse_response("openrouter", raw)["status"], status)
        raw = response(error={"code": 500, "metadata": "SECRET ERROR"})
        result = parse_response("openrouter", raw)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reported_api_cost_usd"], .00012)
        self.assertNotIn("SECRET ERROR", json.dumps(result))
        for content in ('```json\n{}\n```', '{"a":1,"a":2}', '{"a":NaN}'):
            self.assertEqual(parse_response("openrouter", response(content))["output"], content)

    def test_end_to_end_fake_transports_report_v2_and_reported_costs(self):
        plan, jobs, cases, groups = self.plan()
        with tempfile.TemporaryDirectory() as tmp, patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            out = Path(tmp) / "run"
            batch = execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True,
                               confirm_pricing=True, max_usd=1, max_requests=8, environ=ENV, generator=self.fake())
            self.assertEqual(len(batch["records"]), 8)
            self.assertTrue(all(r["cost_usd"] == .00012 for r in batch["records"]))
            self.assertTrue(all(r["actual_provider"] == "Artificial serving provider" for r in batch["records"]))
            summary = load_json(out / "run_summary.json")
            self.assertAlmostEqual(summary["known_reported_api_cost_usd"], .00096)
            self.assertEqual(summary["unknown_cost_attempts"], 0)
            metrics = load_json(out / "report/or_gemini_lite.awareness_form_v2.repeat-1/metrics.json")
            self.assertEqual(metrics["usable_response_rate"], 1)
            self.assertIsNone(metrics["attention"]["accuracy"])
            self.assertNotIn(ENV["OPENROUTER_API_KEY"], (out / "attempts.jsonl").read_text())

    def test_missing_billing_is_unknown_even_with_token_usage(self):
        plan, jobs, cases, groups = self.plan()
        raw = response(usage={"prompt_tokens": 100, "completion_tokens": 50})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            batch = execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True,
                               confirm_pricing=True, max_usd=1, max_requests=8, environ=ENV, generator=self.fake(raw))
            self.assertTrue(all(r["cost_usd"] is None for r in batch["records"]))
            self.assertEqual(load_json(out / "run_summary.json")["unknown_cost_attempts"], 8)

    def test_attention_calls_use_v2_schema_and_roundtrip_in_reports(self):
        plan, jobs, cases, groups = self.plan(case_ids=[c["case_id"] for c in self.cases], queue_ids=["A01", "A02"])
        def fake(model, system, user, schema, limit, timeout, **kwargs):
            output = ({"order": {cid: "Cannot assess" for cid in schema["properties"]["order"]["properties"]},
                       "explanation": "Artificial attention fixture."} if "order" in schema["properties"] else OUTPUT)
            def transport(url, headers, payload, timeout):
                self.assertEqual(payload["response_format"]["json_schema"]["schema"], schema)
                return response(json.dumps(output))
            return generate(model, system, user, schema, limit, timeout, environ=ENV, transport=transport)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            batch = execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True,
                               confirm_pricing=True, max_usd=5, max_requests=48, environ=ENV, generator=fake)
            self.assertEqual(len(batch["records"]), 40)
            self.assertEqual(len(batch["exercises"]), 8)
            self.assertTrue(all(r["status"] == "ok" for r in batch["exercises"]))
            self.assertAlmostEqual(load_json(out / "run_summary.json")["known_reported_api_cost_usd"], 48 * .00012)

    def test_zero_reported_cost_is_known_and_malformed_shapes_are_rejected(self):
        self.assertEqual(parse_response("openrouter", response(usage={"cost": 0}))["reported_api_cost_usd"], 0)
        for raw in (response(choices=[None]), response(choices=[{"message": "invalid"}]),
                    response(choices=[{}, {}]), response(usage="invalid")):
            with self.assertRaises(ProviderError):
                parse_response("openrouter", raw)

    def test_reported_cost_above_reservation_stops_remaining_jobs(self):
        plan, jobs, cases, groups = self.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            batch = execute_v2(plan, jobs, cases, groups, out, consent=True, synthetic=True,
                               confirm_pricing=True, max_usd=1, max_requests=8, environ=ENV,
                               generator=self.fake(response(usage={"cost": 2})))
            self.assertEqual(len(batch["records"]), 1)
            self.assertEqual(load_json(out / "run_summary.json")["stopped"], "cost_exceeded_reservation")

    def test_existing_execution_gates_still_prevent_calls(self):
        plan, jobs, cases, groups = self.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            for gates in ({}, {"consent": True, "synthetic": True, "confirm_pricing": True,
                                "max_usd": 1, "max_requests": 8, "environ": {}},
                          {"consent": True, "synthetic": True, "max_usd": 1, "environ": ENV}):
                with self.assertRaises(ValueError):
                    execute_v2(plan, jobs, cases, groups, out,
                               generator=lambda *a, **k: self.fail("Must not call model"), **gates)
                self.assertFalse(out.exists())

    def test_cli_plan_requires_no_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan"
            result = subprocess.run([sys.executable, "-m", "evals.llm_v2", "plan", "--config", str(CONFIG),
                                     "--out", str(out)], cwd=ROOT, capture_output=True, text=True,
                                    env={"PATH": "/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PLAN ONLY: 8 requests", result.stdout)
            self.assertEqual(load_json(out / "plan.json")["key_presence"], {"openrouter": False})


if __name__ == "__main__":
    unittest.main()
