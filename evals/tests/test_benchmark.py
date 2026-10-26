"""Offline checks of evals/benchmark with a fake model: no keys, no network."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evals.benchmark import build_cases, guardrails, run_live, score, stats


class FakeClient:
    """Answers like the model would, from the report's own numbers."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, response_format, **_):
        given = json.loads(messages[1]["content"])
        if response_format["json_schema"]["name"] == "ems_output":
            text = given["current_transcript"]
            pulse = re.search(r"Pulse (\d+)", text)
            output = {"summary": f"Patient with pulse {pulse.group(1) if pulse else 'unknown'}.",
                      "meaningful_change": "No earlier report", "change_explanation": "First report.",
                      "missing_information": "None identified", "esi_level": "2", "esi_reason": "High risk.",
                      "risk_score": 6, "risk_reason": "Reported vitals."}
        else:
            item = given["new_items"][0]
            pulse = re.search(r"Pulse (\d+)", item["text"])
            vitals = [{"name": "hr", "value": int(pulse.group(1)), "qualifier": "", "item": item["id"],
                       "quote": pulse.group(0)}] if pulse else []
            output = {"chief_complaint": [], "vitals": vitals, "findings": [], "treatments": []}
        message = SimpleNamespace(content=json.dumps(output), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))


class BenchmarkTest(unittest.TestCase):
    def test_kappa_matches_known_values(self):
        perfect = [(1, 1), (2, 2), (3, 3), (4, 4)]
        self.assertAlmostEqual(stats.kappa(perfect, "quadratic")["value"], 1.0)
        # Hand-computed: 2x2 table [[2, 1], [0, 1]] on levels 1-2 -> unweighted kappa 0.5.
        self.assertAlmostEqual(stats.kappa([(1, 1), (1, 1), (1, 2), (2, 2)], "none", levels=(1, 2))["value"], 0.5)

    def test_wilson_interval(self):
        result = stats.wilson(8, 10)
        self.assertAlmostEqual(result["low"], 0.4902, places=3)
        self.assertAlmostEqual(result["high"], 0.9433, places=3)

    def test_under_triage_direction(self):
        result = stats.agreement([(1, 3), (2, 2), (3, 2)])
        self.assertEqual(result["under_triage"]["k"], 1)  # reference 1, AI 3: less urgent
        self.assertEqual(result["over_triage"]["k"], 1)
        self.assertEqual(result["high_acuity_missed_to_3_plus"]["k"], 1)

    def test_spoken_numbers(self):
        self.assertEqual(build_cases.spoken(96), "ninety six")
        self.assertEqual(build_cases.spoken(142), "one forty two")
        self.assertEqual(build_cases.spoken(105), "one oh five")
        self.assertEqual(build_cases.spoken(200), "two hundred")
        self.assertEqual(build_cases.spoken(36.6), "thirty six point six")

    def test_number_check_mutants_catch_wrong_value(self):
        inputs = {"input": build_cases.app_input("X", "Pulse 96, BP 130/82, saturation 95 percent.")}
        fields = {"summary": "Pulse 96, BP 130/82.", "change_explanation": "First.", "esi_reason": "", "risk_reason": ""}
        mutants = list(guardrails.number_mutants(fields, inputs))
        self.assertTrue(mutants)
        for m in mutants:
            self.assertEqual(m["caught"], not m["collision"])
        self.assertTrue(guardrails.fabricated_vital(fields, inputs))

    def test_fake_run_and_score(self):
        from backend import openai_config
        saved = openai_config.openai_client
        openai_config.openai_client = FakeClient()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = Path(tmp) / ".env"
                env.write_text("")
                raw, out = Path(tmp) / "raw", Path(tmp) / "out"
                run_live.main(["--env", str(env), "--out", str(raw), "--limit", "6"])
                run_live.main(["--env", str(env), "--out", str(raw), "--limit", "6"])  # resumes: adds the next 6
                records = [json.loads(line) for line in (raw / "assessments.jsonl").read_text().splitlines()]
                self.assertEqual(len(records), 12)
                self.assertTrue(all(r["status"] == "ok" for r in records))
                self.assertEqual(len({(r["dataset"], r["case_id"], r["repeat"]) for r in records}), 12)
                score.main(["--raw", str(raw), "--out", str(out)])
                metrics = json.loads((out / "metrics.json").read_text())
                self.assertEqual(metrics["esi"]["purav/all"]["outcomes"]["assessed"], 3)  # purav cases come first, 5 repeats each
                self.assertEqual(metrics["esi"]["purav/all"]["ai_levels"], {"2": 3})
                extraction = metrics["extraction"]["mimic"]["per_vital"]["hr"]
                self.assertEqual(extraction["precision"]["value"], 1.0)
        finally:
            openai_config.openai_client = saved


if __name__ == "__main__":
    unittest.main()
