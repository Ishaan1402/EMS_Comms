import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals.contracts import ROOT, load_json


class CLITests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, "-m", "evals", *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=30)

    def test_prepare_baseline_score_without_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for command, out in (("prepare", base / "prepared"), ("baseline", base / "baseline")):
                result = self.run_command(command, "--out", out)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_command("score", "--references", base / "prepared/references.json",
                                      "--predictions", base / "baseline/predictions.json", "--out", base / "score")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = load_json(base / "score/metrics.json")
            self.assertEqual(report["approved_reference_cases"], 0)
            self.assertIsNone(report["results"][0]["preparation"]["accuracy"])
            self.assertEqual(report["results"][0]["usable_response_rate"], 1)
            reviews = load_json(base / "score/summary_review_template.json")
            self.assertEqual(len(reviews["records"]), 10)
            self.assertTrue(all(r["approved"] is False for r in reviews["records"]))

    def test_demo_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo"
            result = self.run_command("demo", "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("MOCK", result.stdout)
            before = (out / "metrics.json").read_bytes()
            result = self.run_command("demo", "--out", out)
            self.assertEqual(result.returncode, 2)
            self.assertEqual((out / "metrics.json").read_bytes(), before)

    def test_notebook_format_and_python_cells_compile(self):
        notebook = load_json(ROOT / "analytics/ems_evaluation.ipynb")
        self.assertEqual(notebook["nbformat"], 4)
        ids = [cell["id"] for cell in notebook["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"notebook:{cell['id']}", "exec")
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])

    def test_run_without_execute_only_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan"
            result = self.run_command("run", "--models", "qwen9b", "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PLAN ONLY", result.stdout)
            self.assertEqual(load_json(out / "plan.json")["planned_requests"], 72)
            self.assertEqual({p.name for p in out.iterdir()}, {"plan.json"})

    def test_execute_missing_consent_or_budget_never_creates_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = self.run_command("run", "--models", "qwen9b", "--execute", "--out", out)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(out.exists())
            result = self.run_command("run", "--models", "qwen9b", "--execute", "--confirm-synthetic", "--out", out)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
