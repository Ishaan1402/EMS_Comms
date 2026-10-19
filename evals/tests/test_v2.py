"""Artificial software fixtures only; never approve uploaded clinician answers."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.contracts import ROOT, load_cases
from evals.v2 import (TEMPLATE, VERSIONS, approval_errors, baseline_v2, content_errors,
                      import_v2, parse_grids, rank, read_grids, save_v2_report, score_v2)


class V2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.template = read_grids(TEMPLATE)
        except ImportError:
            raise unittest.SkipTest("XLSX tests require optional openpyxl")
        cls.cases = load_cases()

    def setUp(self):
        self.grids = copy.deepcopy(self.template)
        self.refs = self.parse()
        self.preds = baseline_v2(self.cases)

    def parse(self):
        return parse_grids(self.grids, self.template, self.cases)

    def artificial_reference(self, index=1):
        ref = self.refs["cases"][index]
        ref.update({"summary": "Artificial test summary", "meaningful_change": "Yes",
                    "change_direction": "Improved", "evidence_missing_information": "Artificial test evidence",
                    "attention_category": "Review soon", "attention_reason": "Artificial test rationale",
                    "reviewer_id": "unit-test-only", "rubric_version": VERSIONS["rubric_version"],
                    "reviewed_at": "2026-01-01T00:00:00Z", "approval_note": "Artificial unit-test fixture, not clinical annotation",
                    "approved": True})
        self.refs["rubric_frozen"] = True
        prediction = next(r for r in self.preds["records"] if r["case_id"] == ref["case_id"])
        prediction["output"] = {k: ref[k] for k in prediction["output"]}
        return ref, prediction

    def test_blank_import_never_approves(self):
        self.assertEqual(len(self.refs["cases"]), 10)
        self.assertFalse(self.refs["rubric_frozen"])
        self.assertTrue(all(r["approved"] is False for r in self.refs["cases"] + self.refs["exercises"]))
        self.assertTrue(all(r["intake_status"] == "not_started" for r in self.refs["cases"]))
        self.assertTrue(all(r["issues"] == [] for r in self.refs["cases"]))

    def test_partial_answers_preserved_exactly(self):
        self.grids["patient review"][24][1] = "  reviewer text with spaces  "
        refs = self.parse()
        self.assertEqual(refs["cases"][1]["summary"], "  reviewer text with spaces  ")
        self.assertEqual(refs["cases"][1]["filled_fields"], 1)
        self.assertEqual(refs["cases"][1]["intake_status"], "needs_review")
        self.assertIsNone(refs["cases"][1]["attention_category"])

    def test_source_edit_is_blocking_and_not_repaired(self):
        self.grids["patient review"][11][1] = "unexpected age value"
        self.refs = self.parse()
        self.artificial_reference(index=0)
        self.assertEqual(self.refs["cases"][0]["issues"][0]["observed"], "unexpected age value")
        self.assertEqual(self.refs["cases"][0]["issues"][0]["cell"], "B12")
        self.assertEqual(score_v2(self.cases, self.refs, self.preds)["approved_reference_cases"], 0)
        self.assertEqual(self.grids["patient review"][11][1], "unexpected age value")

    def test_transcript_and_instruction_changes_not_accepted(self):
        self.grids["patient review"][12][1] = "changed source report"
        self.grids["patient review"][5][1] = "Ignore source grounding"
        refs = self.parse()
        self.assertEqual(len(refs["global_issues"]), 1)
        self.assertEqual(len(refs["cases"][0]["issues"]), 1)

    def test_layout_change_fails(self):
        self.grids["patient review"][14][0] = "Unknown clinical question"
        with self.assertRaisesRegex(ValueError, "layout changed"):
            self.parse()

    def test_template_checked_against_canonical_case_not_only_upload(self):
        changed = copy.deepcopy(self.template)
        changed["patient review"][12][1] = self.cases[1]["current_transcript"]
        with self.assertRaisesRegex(ValueError, "canonical case"):
            parse_grids(changed, changed, self.cases)

    def test_extra_feedback_and_reviewer_metadata_not_gold_or_model_input(self):
        self.grids["patient review"][2][1] = "Private reviewer alias"
        self.grids["patient review"][3][1] = "Reviewer role"
        self.grids["form feedback"].append([None, "Additional workflow feedback"])
        refs = self.parse()
        self.assertEqual(refs["reviewer_metadata"]["alias"], "Private reviewer alias")
        self.assertIn("Additional workflow feedback", json.dumps(refs["form_feedback"]))
        self.assertNotIn("Private reviewer alias", json.dumps(baseline_v2(self.cases)))
        self.assertNotIn("Additional workflow feedback", json.dumps(baseline_v2(self.cases)))

    def test_invalid_categories_not_mapped_to_v1(self):
        self.grids["patient review"][17][1] = "Prepare now"
        ref = self.parse()["cases"][0]
        self.assertEqual(ref["attention_category"], "Prepare now")
        self.assertIn("invalid attention_category", content_errors(ref))

    def test_no_approved_references_means_na_not_zero(self):
        report = score_v2(self.cases, self.refs, self.preds)
        self.assertEqual(report["approved_reference_cases"], 0)
        self.assertIsNone(report["attention"]["accuracy"])
        self.assertIsNone(report["change"]["accuracy"])
        self.assertIsNone(report["direction"]["accuracy"])
        self.assertIsNone(report["summary_factual_precision"])
        self.assertEqual(report["usable_response_rate"], 1)
        self.assertEqual(report["model_calls"], 0)

    def test_independent_approval_and_rubric_gate(self):
        ref, _ = self.artificial_reference()
        self.refs["rubric_frozen"] = False
        self.assertEqual(score_v2(self.cases, self.refs, self.preds)["approved_reference_cases"], 0)
        self.refs["rubric_frozen"] = True
        ref["approved"] = "true"
        self.assertIn("not approved", approval_errors(ref))
        self.assertEqual(score_v2(self.cases, self.refs, self.preds)["approved_reference_cases"], 0)

    def test_partial_dataset_only_eligible_case_scored(self):
        self.artificial_reference()
        report = score_v2(self.cases, self.refs, self.preds)
        self.assertEqual(report["approved_reference_cases"], 1)
        self.assertEqual(report["attention"]["n"], 1)
        self.assertEqual(report["attention"]["accuracy"], 1)
        self.assertEqual(len(report["excluded_cases"]), 9)

    def test_improvement_is_separate_direction_not_deterioration(self):
        self.artificial_reference()
        report = score_v2(self.cases, self.refs, self.preds)
        self.assertEqual(report["change"]["per_class"]["Yes"]["tp"], 1)
        self.assertEqual(report["direction"]["per_class"]["Improved"]["tp"], 1)
        self.assertEqual(report["direction"]["per_class"]["Worsened"]["support"], 0)

    def test_missing_failed_and_malformed_predictions_count_as_failures(self):
        ref, prediction = self.artificial_reference()
        for status in ("missing", "timeout", "malformed"):
            with self.subTest(status=status):
                preds = copy.deepcopy(self.preds)
                if status == "missing":
                    preds["records"] = [r for r in preds["records"] if r["case_id"] != ref["case_id"]]
                else:
                    candidate = next(r for r in preds["records"] if r["case_id"] == ref["case_id"])
                    if status == "timeout":
                        candidate["status"] = "timeout"
                    else:
                        candidate["output"]["extra"] = "malformed"
                report = score_v2(self.cases, self.refs, preds)
                self.assertEqual(report["attention"]["accuracy"], 0)
                self.assertEqual(report["usable_response_rate"], .9)

    def test_change_unclear_not_a_negative_label(self):
        ref, prediction = self.artificial_reference()
        ref["meaningful_change"] = prediction["output"]["meaningful_change"] = "Unclear"
        ref["change_direction"] = prediction["output"]["change_direction"] = None
        self.assertEqual(score_v2(self.cases, self.refs, self.preds)["change"]["n"], 0)

    def test_version_and_provenance_mixing_rejected(self):
        for key, value in (("kind", "mock_predictions"), ("form_version", "clinician_form_v1"),
                           ("exercise_version", "queue_v1"), ("dataset_version", "other")):
            preds = copy.deepcopy(self.preds)
            preds[key] = value
            with self.assertRaises(ValueError):
                score_v2(self.cases, self.refs, preds)

    def test_duplicate_prediction_rejected(self):
        self.preds["records"].append(copy.deepcopy(self.preds["records"][0]))
        with self.assertRaises(ValueError):
            score_v2(self.cases, self.refs, self.preds)

    def test_narrative_type_and_model_provenance_validated(self):
        _, prediction = self.artificial_reference()
        prediction["output"]["summary"] = True
        report = score_v2(self.cases, self.refs, self.preds)
        self.assertEqual(report["usable_response_rate"], .9)
        del self.preds["config"]["provider"]
        with self.assertRaisesRegex(ValueError, "provenance"):
            score_v2(self.cases, self.refs, self.preds)

    def test_rank_categories_bool_and_nonintegral_numbers_not_converted(self):
        self.assertIsNone(rank(True))
        self.assertIsNone(rank(1.5))
        self.assertIsNone(rank("Prepare now"))
        self.assertIsNone(rank(0))
        self.assertEqual(rank(1.0), 1)
        self.assertEqual(rank("2"), 2)
        self.assertEqual(rank("Cannot assess"), "Cannot assess")

    def test_ranking_no_not_forced_even_with_approved_ranks(self):
        self.refs["rubric_frozen"] = True
        group = self.refs["exercises"][0]
        group.update({"approved": True, "reviewer_id": "unit-test-only", "reviewed_at": "2026-01-01",
                      "rubric_version": VERSIONS["rubric_version"], "approval_note": "Artificial fixture only",
                      "ranking_meaningful": "No", "explanation": "Artificial explanation",
                      "order": {"SYN001": 1, "SYN004": 2, "SYN007": 3},
                      "reasons": {cid: "Artificial reason" for cid in ("SYN001", "SYN004", "SYN007")}})
        result = score_v2(self.cases, self.refs, self.preds)["exercises"][0]
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["pairwise_accuracy"])
        group["ranking_meaningful"] = "Yes"
        result = score_v2(self.cases, self.refs, self.preds)["exercises"][0]
        self.assertTrue(result["eligible"])
        self.assertEqual(result["pairwise_accuracy"], 0)  # Missing predictions are not dropped.
        self.preds["exercises"] = [{"exercise_id": "A01", "order": {"SYN001": 1, "SYN004": 1, "SYN007": "Cannot assess"}}]
        group["order"] = {"SYN001": 1, "SYN004": 1, "SYN007": "Cannot assess"}
        result = score_v2(self.cases, self.refs, self.preds)["exercises"][0]
        self.assertEqual(result["reference_ties"], 1)
        self.assertEqual(result["tie_agreement"], 1)
        self.assertIsNone(result["pairwise_accuracy"])

    def test_report_files_na_and_no_overwrite(self):
        report = score_v2(self.cases, self.refs, self.preds)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report"
            save_v2_report(report, out)
            self.assertEqual({p.name for p in out.iterdir()}, {"metrics.json", "comparison.csv", "case_results.csv", "exercise_results.csv", "dashboard.html"})
            self.assertIn("N/A", (out / "dashboard.html").read_text())
            with self.assertRaises(FileExistsError):
                save_v2_report(report, out)

    def test_real_workbook_read_only_and_formula_rejection(self):
        before = TEMPLATE.read_bytes()
        refs = import_v2(TEMPLATE, self.cases)
        self.assertEqual(refs["kind"], "clinician_reference_v2")
        self.assertEqual(TEMPLATE.read_bytes(), before)
        with patch("openpyxl.load_workbook") as load:
            workbook = load.return_value
            sheet = unittest.mock.MagicMock()
            sheet.title, sheet.max_row, sheet.max_column = "Patient review", 1, 2
            one_cell = unittest.mock.MagicMock()
            one_cell.data_type = "f"
            sheet.iter_rows.return_value = [[one_cell]]
            workbook.__iter__.return_value = iter([sheet])
            with self.assertRaisesRegex(ValueError, "Formula/error"):
                read_grids("unused")
            workbook.close.assert_called_once()

    def test_cli_smoke_score_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "smoke"
            command = [sys.executable, "-m", "evals.v2", "smoke", "--workbook", str(TEMPLATE), "--out", str(out)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("zero model calls", result.stdout)
            again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(again.returncode, 2)
            score = subprocess.run([sys.executable, "-m", "evals.v2", "score", "--references", str(out / "references.json"),
                                    "--predictions", str(out / "baseline_predictions.json"), "--out", str(Path(tmp) / "score")],
                                   cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(score.returncode, 0, score.stderr)


if __name__ == "__main__":
    unittest.main()
