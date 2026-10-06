import copy
import json
import tempfile
import unittest
from pathlib import Path

from evals.annotations import import_workbook, parse_doctor_rows, parse_queue_rows
from evals.contracts import ROOT, blank_reference, load_cases, load_json, output_errors, reference_errors
from evals.demo import mock_data, raw_report_baseline
from evals.evaluate import evaluate
from evals.metrics import classification_metrics, pairwise_metrics
from evals.report import render_report, save_report


class MetricsTests(unittest.TestCase):
    def test_perfect(self):
        result = classification_metrics(["Yes", "No"], ["Yes", "No"], ("Yes", "No"))
        self.assertEqual(result["accuracy"], 1)
        self.assertEqual(result["macro_f1"], 1)

    def test_standard_counts(self):
        result = classification_metrics(["Yes", "Yes", "No", "No"], ["Yes", "No", "Yes", "No"], ("Yes", "No"))
        positive = result["per_class"]["Yes"]
        self.assertEqual((positive["tp"], positive["fp"], positive["fn"]), (1, 1, 1))
        self.assertEqual((positive["precision"], positive["recall"], positive["f1"]), (.5, .5, .5))

    def test_failures_not_dropped(self):
        result = classification_metrics(["Yes", "No"], ["[missing]", "Unclear"], ("Yes", "No"))
        self.assertEqual(result["accuracy"], 0)
        self.assertEqual(result["per_class"]["Yes"]["false_negative_rate"], 1)
        self.assertIsNone(result["per_class"]["Yes"]["precision"])
        self.assertIn("[missing]", result["confusion_matrix"]["columns"])

    def test_empty_is_unavailable(self):
        result = classification_metrics([], [], ("Yes", "No"))
        self.assertIsNone(result["accuracy"])
        self.assertIsNone(result["macro_f1"])

    def test_pairs_ties_unknown(self):
        result = pairwise_metrics({"a": 1, "b": 1, "c": 2, "d": "Unsure"}, {"a": 1, "b": 1, "c": 2})
        self.assertEqual(result["ordered_pairs"], 2)
        self.assertEqual(result["reference_ties"], 1)
        self.assertEqual(result["indeterminate_pairs"], 3)
        self.assertEqual(result["pairwise_accuracy"], 1)
        self.assertEqual(result["tie_agreement"], 1)

    def test_missing_pairs_wrong(self):
        result = pairwise_metrics({"a": 1, "b": 2, "c": 3}, {})
        self.assertEqual(result["ordered_pairs"], 3)
        self.assertEqual(result["pairwise_accuracy"], 0)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_cases()
        self.queues = load_json(ROOT / "evals/queues.json")
        self.refs, self.preds, self.reviews = mock_data(self.cases, self.queues)

    def score(self, reviews=True):
        return evaluate(self.cases, self.refs, self.preds, self.queues,
                        self.reviews if reviews else None, allow_mock=True)

    def test_mock_requires_opt_in(self):
        with self.assertRaises(ValueError):
            evaluate(self.cases, self.refs, self.preds, self.queues)

    def test_mock_cannot_mix_with_clinical(self):
        self.refs["kind"] = "clinician_reference"
        with self.assertRaises(ValueError):
            self.score()

    def test_perfect_and_flawed_demo(self):
        perfect, flawed, failure = self.score()["results"]
        self.assertEqual(perfect["change"]["accuracy"], 1)
        self.assertEqual(perfect["preparation"]["accuracy"], 1)
        self.assertEqual(perfect["queue"]["pairwise_accuracy"], 1)
        self.assertEqual(perfect["summary"]["content_recall"], 1)
        self.assertLess(flawed["preparation"]["accuracy"], 1)
        self.assertEqual(flawed["status_counts"]["missing"], 1)
        self.assertEqual(failure["usable_response_rate"], 0)
        self.assertEqual(failure["preparation"]["accuracy"], 0)
        self.assertIsNone(failure["schema_validation_rate"])

    def test_unapproved_answers_not_scored(self):
        for row in self.refs["cases"]:
            row["approved"] = False
        result = self.score(reviews=False)
        self.assertEqual(result["approved_reference_cases"], 0)
        self.assertIsNone(result["results"][0]["preparation"]["accuracy"])
        self.assertIsNone(result["results"][0]["change"]["accuracy"])

    def test_done_is_not_approval(self):
        ref = copy.deepcopy(self.refs["cases"][0])
        ref["approved"] = False
        self.assertIn("not approved", reference_errors(ref))
        ref["approved"] = "true"
        self.assertIn("not approved", reference_errors(ref))

    def test_no_earlier_and_unclear_not_binary_negatives(self):
        result = self.score(reviews=False)["results"][0]
        assessable = sum(r["meaningful_change"] in ("Yes", "No") for r in self.refs["cases"])
        self.assertEqual(result["change"]["n"], assessable * 3)

    def test_duplicate_predictions_rejected(self):
        self.preds["records"].append(copy.deepcopy(self.preds["records"][0]))
        with self.assertRaises(ValueError):
            self.score()

    def test_unknown_case_rejected(self):
        self.preds["records"][0]["case_id"] = "UNKNOWN"
        with self.assertRaises(ValueError):
            self.score(reviews=False)

    def test_stale_summary_grade_rejected(self):
        self.preds["records"][0]["output"]["summary"] = "Changed output"
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.score()

    def test_mixed_rubrics_rejected(self):
        self.refs["cases"][0]["rubric_version"] = "different"
        with self.assertRaises(ValueError):
            self.score()

    def test_summary_unavailable_without_reviews(self):
        result = self.score(reviews=False)["results"][0]
        self.assertEqual(result["summary"]["reviewed_responses"], 0)
        self.assertIsNone(result["summary"]["factual_precision"])

    def test_incomplete_approved_reference_excluded(self):
        self.refs["cases"][0]["reference_summary"] = None
        self.assertEqual(self.score(reviews=False)["approved_reference_cases"], 9)

    def test_invalid_rank_not_silently_accepted(self):
        self.preds["queues"][0]["order"]["SYN001"] = True
        q = self.score(reviews=False)["results"][0]["queue"]["details"][0]
        self.assertFalse(q["prediction_valid"])
        self.assertEqual(q["pairwise_accuracy"], 0)

    def test_queue_abstention_preserves_other_pairs(self):
        self.preds["queues"][0]["order"]["SYN001"] = "Unsure"
        q = self.score(reviews=False)["results"][0]["queue"]["details"][0]
        self.assertTrue(q["prediction_valid"])
        self.assertEqual(q["ordered_pairs"], 3)
        self.assertEqual(q["correct_pairs"], 1)

    def test_float_repeat_count_rejected(self):
        self.preds["configs"][0]["repeats"] = 3.0
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.score(reviews=False)

    def test_float_repeat_id_rejected(self):
        self.preds["records"][0]["repeat_id"] = 1.0
        with self.assertRaisesRegex(ValueError, "repeat_id"):
            self.score(reviews=False)

    def test_nonfinite_cost_rejected(self):
        self.preds["records"][0]["cost_usd"] = float("nan")
        with self.assertRaises(ValueError):
            self.score(reviews=False)

    def test_output_contract(self):
        self.assertTrue(output_errors(None))
        self.assertTrue(output_errors({"summary": "Only summary"}))

    def test_dashboard_escapes_outputs(self):
        self.preds["records"][0]["output"]["summary"] = "<script>alert(1)</script>"
        report = self.score(reviews=False)
        page = render_report(report)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("MOCK", page)

    def test_report_roundtrip_and_no_overwrite(self):
        report = self.score()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report"
            save_report(report, out)
            self.assertEqual(load_json(out / "metrics.json")["mode"], report["mode"])
            self.assertTrue((out / "comparison.csv").exists())
            with self.assertRaises(FileExistsError):
                save_report(report, out)

    def test_json_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json(path)

    def test_real_reference_missing_labels_has_no_scores(self):
        ref = {"kind": "clinician_reference", "dataset_version": "pilot_v1",
               "cases": [blank_reference(c["case_id"]) for c in self.cases], "queues": []}
        pred = raw_report_baseline(self.cases)
        report = evaluate(self.cases, ref, pred, self.queues)
        self.assertEqual(report["approved_reference_cases"], 0)
        self.assertEqual(report["results"][0]["usable_response_rate"], 1)
        self.assertIsNone(report["results"][0]["preparation"]["accuracy"])

    def test_actual_workbook_import_blank_and_unapproved(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("Optional openpyxl not installed")
        reference = import_workbook(ROOT / "docs/clinician-annotation/Annotation_Notebook.xlsx", self.cases, self.queues)
        self.assertEqual(len(reference["cases"]), 10)
        self.assertEqual(len(reference["queues"]), 2)
        self.assertTrue(all(r["reference_summary"] is None and r["approved"] is False for r in reference["cases"]))

    def form_rows(self):
        rows = []
        for case in self.cases:
            rows.extend([[f"Case 1 · {case['case_id']}", None],
                         ["Earlier report", case["prior_information"]], ["Current report", case["current_transcript"]],
                         ["1. Brief ED handoff\n2–3 sentences", None], ["2. Important change?", None],
                         ["Why? What is missing?\nOne sentence is enough", None], ["3. Preparation needed?", None],
                         ["Review status", "Not started"]])
        return rows

    def test_shifted_form_rows_and_partial_answers(self):
        rows = self.form_rows()
        rows.insert(0, ["Extra instruction row", None])
        rows[4][1] = "Doctor's partial summary"
        refs = parse_doctor_rows(rows, self.cases)
        self.assertEqual(refs[0]["reference_summary"], "Doctor's partial summary")
        self.assertIsNone(refs[0]["meaningful_change"])
        self.assertFalse(refs[0]["approved"])

    def test_changed_transcript_rejected(self):
        rows = self.form_rows()
        rows[2][1] = "Changed report"
        with self.assertRaisesRegex(ValueError, "Source report changed"):
            parse_doctor_rows(rows, self.cases)

    def test_changed_queue_source_rejected(self):
        rows, source = [], {c["case_id"]: c for c in self.cases}
        for queue in self.queues:
            rows.append([queue["form_heading"], None])
            for case_id in queue["case_ids"]:
                case = source[case_id]
                rows.extend([[f"{case_id} · ETA {case['eta_minutes']} minutes", None],
                             ["Earlier report", case["prior_information"]],
                             ["Current report", case["current_transcript"]], ["Preparation order", None]])
            rows.extend([["Why this order?", None], ["Review status", "Not started"]])
        self.assertEqual(len(parse_queue_rows(rows, self.queues, self.cases)), 2)
        rows[3][1] = "Changed report"
        with self.assertRaisesRegex(ValueError, "Queue source report changed"):
            parse_queue_rows(rows, self.queues, self.cases)


if __name__ == "__main__":
    unittest.main()
