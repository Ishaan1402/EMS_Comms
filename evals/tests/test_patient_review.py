import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from evals.annotations import import_workbook, parse_patient_rows
from evals.contracts import ROOT, case_dataset_version, load_cases, load_json
from evals.demo import raw_report_baseline
from evals.evaluate import evaluate
from evals.experiments import build_plan, case_input


CASES = ROOT / "docs/clinician-annotation/patient_review_v2_1_cases.json"


def form_rows(cases):
    rows = [["Dataset version", "patient_review_v2.1"]]
    for index, case in enumerate(cases, 1):
        rows += [[f"Case {index} / {case['case_id']}", f"ETA {case['eta_minutes']} minutes at latest report"],
                 ["Age / reported sex / weight", case["patient_details"]]]
        if case["prior_information"] != "No earlier report provided.":
            rows.append([f"T-{case['elapsed_minutes']} min\nEMS Field Report", case["prior_information"]])
        rows += [["T=0\nUpdated EMS Field Report", case["current_transcript"]],
                 ["EMS to ED Handoff", ""], ["Meaningful change?", ""], ["Direction (if Yes)", ""],
                 ["Evidence / missing information", ""], ["Preparation needed?", ""],
                 ["Reason for preparation / limits", ""], ["Review status", "Not started"], ["", ""]]
    return rows


class PatientReviewTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_cases(CASES)
        self.rows = form_rows(self.cases)

    def set_answer(self, label, value):
        next(row for row in self.rows if row[0] == label)[1] = value

    def test_partial_answers_and_optional_direction_survive_without_approval(self):
        self.set_answer("EMS to ED Handoff", "Partial clinician handoff, still under discussion.")
        self.set_answer("Preparation needed?", "Cannot assess")
        self.set_answer("Evidence / missing information", "Important history is not established.")
        self.set_answer("Reason for preparation / limits", "I cannot select preparation needs yet.")
        self.set_answer("Review status", "Done")
        reference = parse_patient_rows(self.rows, self.cases)[0]
        self.assertEqual(reference["reference_summary"], "Partial clinician handoff, still under discussion.")
        self.assertEqual(reference["preparation_category"], "Unsure")
        self.assertEqual(reference["preparation_category_display"], "Cannot assess")
        self.assertIsNone(reference["change_direction"])
        self.assertIn("Important history is not established.", reference["explanation_missing_information"])
        self.assertIn("I cannot select preparation needs yet.", reference["explanation_missing_information"])
        self.assertFalse(reference["approved"])
        self.assertIsNone(reference["reviewer_id"])

    def test_all_app_display_categories_import(self):
        for choice in ("Prepare now", "Can wait", "Routine", "Cannot assess"):
            with self.subTest(choice=choice):
                self.set_answer("Preparation needed?", choice)
                reference = parse_patient_rows(self.rows, self.cases)[0]
                self.assertEqual(reference["preparation_category"], "Unsure" if choice == "Cannot assess" else choice)
                self.assertEqual(reference["preparation_category_display"], choice)

    def test_review_urgency_is_not_silently_translated_to_preparation(self):
        self.set_answer("Preparation needed?", "Immediate review")
        with self.assertRaisesRegex(ValueError, "Invalid preparation_category"):
            parse_patient_rows(self.rows, self.cases)

    def test_demographic_edit_is_rejected_instead_of_grading_different_inputs(self):
        self.set_answer("Age / reported sex / weight", "5 years / male. Weight 20 kg.")
        with self.assertRaisesRegex(ValueError, "patient_details changed"):
            parse_patient_rows(self.rows, self.cases)

    def test_changed_medication_number_in_report_is_rejected(self):
        row = next(row for row in self.rows if "324 mg" in row[1])
        row[1] = row[1].replace("324 mg", "32 mg")
        with self.assertRaisesRegex(ValueError, "current_transcript changed"):
            parse_patient_rows(self.rows, self.cases)

    def test_missing_earlier_report_is_rejected(self):
        row = next(row for row in self.rows if row[0].startswith("T-8 min"))
        self.rows.remove(row)
        with self.assertRaisesRegex(ValueError, "Incomplete/changed"):
            parse_patient_rows(self.rows, self.cases)

    def test_eta_or_interval_edits_are_rejected(self):
        self.rows[1][1] = "ETA 2 minutes at latest report"
        with self.assertRaisesRegex(ValueError, "ETA changed"):
            parse_patient_rows(self.rows, self.cases)
        self.rows = form_rows(self.cases)
        next(row for row in self.rows if row[0].startswith("T-8 min"))[0] = "T-3 min\nEMS Field Report"
        with self.assertRaisesRegex(ValueError, "interval changed"):
            parse_patient_rows(self.rows, self.cases)

    def test_old_dataset_or_duplicate_answer_cannot_be_imported_as_new(self):
        self.rows[0][1] = "pilot_v1"
        with self.assertRaisesRegex(ValueError, "dataset versions differ"):
            parse_patient_rows(self.rows, self.cases)
        self.rows = form_rows(self.cases)
        self.rows.insert(5, ["Preparation needed?", "Routine"])
        with self.assertRaisesRegex(ValueError, "Duplicate Patient review field"):
            parse_patient_rows(self.rows, self.cases)

    def test_additional_unmatched_patient_information_is_rejected(self):
        self.rows.insert(4, ["Other patient history", "New diagnosis not present in the app input."])
        with self.assertRaisesRegex(ValueError, "Unexpected Patient review field"):
            parse_patient_rows(self.rows, self.cases)

    def test_unversioned_or_mixed_fixtures_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "versioned case fixtures"):
            parse_patient_rows(self.rows, load_cases())
        self.cases[0]["dataset_version"] = "pilot_v1"
        with self.assertRaisesRegex(ValueError, "versions differ"):
            case_dataset_version(self.cases)

    def test_changed_cases_cannot_be_scored_with_old_matching_reference_prediction_versions(self):
        references = {"kind": "clinician_reference", "dataset_version": "pilot_v1", "cases": [], "queues": []}
        predictions = raw_report_baseline(self.cases, "pilot_v1")
        with self.assertRaisesRegex(ValueError, "Case dataset version"):
            evaluate(self.cases, references, predictions, [])

    def test_old_experiment_configuration_rejected_before_calls(self):
        with self.assertRaisesRegex(ValueError, "Case dataset version"):
            build_plan(load_json(ROOT / "evals/experiments.json"), self.cases, [])

    def test_model_input_does_not_include_form_metadata_or_possible_annotations(self):
        case = {**self.cases[0], "reference_summary": "Never show this clinician answer to the model."}
        payload = case_input(case)
        self.assertNotIn("dataset_version", payload)
        self.assertNotIn("patient_details", payload)
        self.assertNotIn("reference_summary", payload)
        self.assertIn("80 kg", payload["current_transcript"])
        recovering = next(case for case in self.cases if case["case_id"] == "SYN006")
        self.assertNotIn("68 kg", recovering["prior_information"])
        self.assertIn("68 kg", recovering["current_transcript"])

    def test_patient_workbook_import_ignores_archived_responses_and_rejects_formulas(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("Optional openpyxl not installed")
        book = MagicMock()
        book.sheetnames = ["Patient review", "Original v2"]
        sheet = MagicMock()
        sheet.max_row = len(self.rows)
        cells = [tuple(SimpleNamespace(value=value, data_type="s", row=i) for value in row)
                 for i, row in enumerate(self.rows, 1)]
        sheet.iter_rows.side_effect = lambda **kwargs: iter(cells)
        book.__getitem__.side_effect = lambda name: sheet if name == "Patient review" else self.fail("Read archived answers")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "mock-source.bin"
            source.write_bytes(b"Mocked reader source for hash, not an authored workbook")
            with patch("openpyxl.load_workbook", return_value=book):
                reference = import_workbook(source, self.cases, [])
                self.assertEqual(reference["dataset_version"], "patient_review_v2.1")
                self.assertEqual(len(reference["cases"]), 10)
                self.assertEqual(reference["queues"], [])
                self.assertTrue(all(row["approved"] is False for row in reference["cases"]))
                with self.assertRaisesRegex(ValueError, "Missing required review tabs"):
                    import_workbook(source, self.cases, [{"queue_id": "Q01"}])
                cells[4][1].data_type = "f"
                with self.assertRaisesRegex(ValueError, "Formula in source/answer area"):
                    import_workbook(source, self.cases, [])
        self.assertTrue(book.close.called)

    def test_attention_rows_and_complete_answers_are_approved_deterministically(self):
        from evals.annotations import approve_complete, parse_attention_rows
        groups = load_json(ROOT / "docs/clinician-annotation/patient_review_v2_1_queues.json")
        source = {c["case_id"]: c for c in self.cases}
        rows = []
        for number, group in enumerate(groups, 1):
            rows += [[f"Group {number} / {group['queue_id']}", "Separate scenario."],
                     ["Is ranking meaningful here?", "Yes"], ["Why / missing context?", "Bed state unknown."]]
            for rank, case_id in enumerate(group["case_ids"], 1):
                case = source[case_id]
                rows += [[case_id, f"ETA {case['eta_minutes']} minutes at latest report"],
                         ["Age / reported sex / weight", case["patient_details"]],
                         ["T=0\nEMS Field Report", case["current_transcript"]],
                         ["Attention order", "Cannot assess" if rank == 3 else str(rank)],
                         ["Reason for position / tie / limits", f"Reason {rank}."]]
        queues = parse_attention_rows(rows, groups, self.cases, "Dr Test")
        self.assertEqual(queues[0]["order"], {"SYN001": 1, "SYN004": 2, "SYN007": "Unsure"})
        self.assertEqual(queues[0]["status"], "Done")
        self.assertIn("SYN004: Reason 2.", queues[0]["explanation"])
        changed = [list(row) for row in rows]
        changed[5][1] = changed[5][1].replace("46", "47")
        with self.assertRaisesRegex(ValueError, "Attention source"):
            parse_attention_rows(changed, groups, self.cases)

        self.rows.insert(0, ["Reviewer alias", "Dr Test"])
        for label, value in (("EMS to ED Handoff", "Handoff."), ("Meaningful change?", "No earlier report"),
                             ("Evidence / missing information", "Gaps."), ("Preparation needed?", "Routine"),
                             ("Review status", "Done")):
            self.set_answer(label, value)
        reference = {"dataset_version": "patient_review_v2.1", "cases": parse_patient_rows(self.rows, self.cases),
                     "queues": queues}
        skipped = approve_complete(reference)
        self.assertTrue(reference["cases"][0]["approved"])
        self.assertEqual(reference["cases"][0]["reviewer_id"], "Dr Test")
        self.assertEqual(reference["cases"][0]["rubric_version"], "patient_review_v2.1")
        self.assertFalse(reference["cases"][1]["approved"])
        self.assertTrue(all(q["approved"] for q in queues))
        self.assertEqual(len(skipped), 9)


if __name__ == "__main__":
    unittest.main()
