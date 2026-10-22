"""Artificial software fixtures. No approvals, generation, classifiers or model APIs."""

import copy
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from evals.annotations_v21 import DATASET_VERSION, parse_patient_rows
from evals.contracts import ROOT, load_json
from evals.tfidf_prep import (COMMON_STOP_WORDS, LEGACY_FIXED_PARAMS, canonical_hash, csv_file, fit_features, grouped_split,
                             input_text, prepare, transform_records)


def artificial_rows():
    rows = [["Dataset version", DATASET_VERSION], ["Reviewer alias", "REVIEWER_SECRET"]]
    for i, label in enumerate([1, 1, 2, 2, 2, 2, 3, 3, 3, 3], 1):
        rows.extend([[f"Case {i} / SYN{i:03}", f"ETA {i} minutes at latest report"],
                     ["Age / reported sex / weight", "Not supplied / not supplied / not supplied"],
                     ["T=0\nEMS Field Report", f"Artificial source symptom{i} pulse {70+i} report {i}. Not clinical evidence."],
                     ["EMS to ED Handoff", "DOCTOR_ANSWER_SECRET"], ["Meaningful change?", "No earlier report"],
                     ["Direction (if Yes)", None], ["Evidence / missing information", "ANNOTATION_SECRET"],
                     ["ESI level (1–5)", float(label)], ["ESI reason / limits", "EXPLANATION_SECRET"],
                     ["Review status", "Done"]])
    return rows


def artificial_import():
    return {**parse_patient_rows(artificial_rows()), "source_sha256": "artificial-software-fixture"}


class ImportV21Tests(unittest.TestCase):
    def test_import_new_sources_labels_and_no_approval(self):
        result = artificial_import()
        self.assertEqual(len(result["cases"]), 10)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["esi_counts"], {"1": 2, "2": 4, "3": 4})
        self.assertEqual(result["absent_esi_levels"], [4, 5])
        self.assertTrue(all(r["approved"] is False for r in result["references"]))
        serialized = json.dumps(result["cases"])
        for secret in ("REVIEWER_SECRET", "DOCTOR_ANSWER_SECRET", "ANNOTATION_SECRET", "EXPLANATION_SECRET"):
            self.assertNotIn(secret, serialized)
        self.assertIn("DOCTOR_ANSWER_SECRET", json.dumps(result["references"]))

    def test_duplicate_case_or_field_or_wrong_version_rejected(self):
        for mutation in (lambda rows: rows.append(["Case 11 / SYN001", "ETA 1 minutes at latest report"]),
                         lambda rows: rows.append(["ESI level (1–5)", 3]),
                         lambda rows: rows.__setitem__(0, ["Dataset version", "pilot_v1"]),
                         lambda rows: rows.append(["Unknown case field", "unexpected"])):
            rows = artificial_rows()
            mutation(rows)
            with self.assertRaises(ValueError):
                parse_patient_rows(rows)

    def test_missing_sources_targets_and_incomplete_answers_are_not_invented(self):
        for label, value, problem in (("T=0\nEMS Field Report", None, "current_transcript"),
                                      ("ESI level (1–5)", "2", "esi_level"),
                                      ("ESI level (1–5)", True, "esi_level"),
                                      ("ESI level (1–5)", 6, "esi_level"),
                                      ("Review status", "Question for team", "review_status"),
                                      ("Meaningful change?", "Yes", "meaningful_change")):
            rows = artificial_rows()
            next(row for row in rows if row[0] == label)[1] = value
            result = parse_patient_rows(rows)
            self.assertTrue(any(i["field"] == problem for i in result["issues"]))

    def test_longitudinal_reports_and_raw_direction_are_preserved(self):
        rows = artificial_rows()
        start = next(i for i, r in enumerate(rows) if r[0] == "T=0\nEMS Field Report")
        rows.insert(start, ["T-8 min\nEMS Field Report", "Earlier report exactly as supplied"])
        next(r for r in rows if r[0] == "Meaningful change?")[1] = "Yes"
        next(r for r in rows if r[0] == "Direction (if Yes)")[1] = "Deterioration"
        result = parse_patient_rows(rows)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["cases"][0]["elapsed_minutes"], 8)
        self.assertEqual(result["cases"][0]["prior_information"], "Earlier report exactly as supplied")
        self.assertEqual(result["references"][0]["change_direction"], "Deterioration")


class TfidfPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import sklearn
            import numpy
        except ImportError:
            raise unittest.SkipTest("TF-IDF tests need optional evals/requirements-ml.txt")

    def setUp(self):
        self.imported = artificial_import()
        self.cases = self.imported["cases"]
        self.refs = self.imported["references"]

    def test_seeded_split_reproducible_and_each_training_class_retained(self):
        split = grouped_split(self.cases, self.refs)
        self.assertEqual(split, grouped_split(list(reversed(self.cases)), list(reversed(self.refs))))
        self.assertEqual(len(split["train_ids"]), 7)
        self.assertEqual(len(split["test_ids"]), 3)
        self.assertFalse(set(split["train_ids"]) & set(split["test_ids"]))
        self.assertEqual(set(split["training_class_counts"]), {"1", "2", "3"})
        self.assertEqual(split["missing_test_classes"], [])

    def test_explicit_group_map_prevents_family_leakage(self):
        groups = {c["case_id"]: c["case_id"] for c in self.cases}
        groups.update({"SYN003": "shared_family", "SYN004": "shared_family"})
        split = grouped_split(self.cases, self.refs, group_map=groups)
        self.assertFalse({groups[c] for c in split["train_ids"]} & {groups[c] for c in split["test_ids"]})
        with self.assertRaisesRegex(ValueError, "same scenario family"):
            grouped_split(self.cases, self.refs, group_map=groups, test_ids=["SYN003"])
        with self.assertRaises(ValueError):
            grouped_split(self.cases, self.refs, group_map={"SYN001": "one"})

    def test_invalid_splits_and_feature_limits_fail(self):
        for options in ({"seed": -1}, {"test_fraction": 0}, {"test_fraction": float("nan")},
                        {"test_ids": ["SYN001", "SYN002"]}, {"test_ids": ["UNKNOWN"]},
                        {"test_ids": ["SYN001", "SYN001"]}):
            with self.assertRaises(ValueError):
                grouped_split(self.cases, self.refs, **options)
        for limit in (0, True, 257):
            with self.assertRaises(ValueError):
                fit_features(self.cases[:7], self.cases[7:], max_features=limit)

    def test_holdout_vocabulary_and_annotations_never_enter_fitting(self):
        train, test = copy.deepcopy(self.cases[:7]), copy.deepcopy(self.cases[7:])
        for c in test:
            c["current_transcript"] += " HOLDOUTONLYTOKEN"
        for c in train:
            c.update({"reference_summary": "LEAKANSWER", "esi_reason": "LEAKREASON", "esi_level": "LEAKLABEL"})
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            _, _, state, _ = fit_features(train, test, max_features=100)
        self.assertNotIn("holdoutonlytoken", state["vocabulary"])
        for c in train:
            self.assertNotIn("LEAK", input_text(c))
        self.assertTrue(all("leak" not in term for term in state["vocabulary"]))
        test[0]["current_transcript"] += " MOREHELDOUTTEXT"
        self.assertEqual(state, fit_features(train, test, max_features=100)[2])

    def test_json_vectorizer_roundtrip_and_tamper_rejection(self):
        import numpy as np
        _, matrix, state, _ = fit_features(self.cases[:7], self.cases[7:])
        restored = transform_records(self.cases[7:], json.loads(json.dumps(state)))
        np.testing.assert_allclose(matrix, restored, rtol=0, atol=1e-12)
        state["idf"][0] += 1
        with self.assertRaisesRegex(ValueError, "hash"):
            transform_records(self.cases[7:], state)
        state["state_sha256"] = canonical_hash({k: v for k, v in state.items() if k != "state_sha256"})
        state["parameters"]["input"] = "filename"
        state["state_sha256"] = canonical_hash({k: v for k, v in state.items() if k != "state_sha256"})
        with self.assertRaisesRegex(ValueError, "parameters"):
            transform_records(self.cases[7:], state)

    def test_common_words_removed_but_clinical_negation_and_numbers_retained(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from evals.tfidf_prep import FIXED_PARAMS
        params = {**FIXED_PARAMS, "ngram_range": (1, 2)}
        analyzer = TfidfVectorizer(**params).build_analyzer()
        tokens = analyzer("He reports no chest pain and she is not without oxygen before 6 after 30 now unable cannot may only")
        for word in ("he", "reports", "and", "she", "is"):
            self.assertNotIn(word, tokens)
        for word in ("no", "not", "without", "before", "after", "now", "unable", "cannot", "may", "only", "6", "30"):
            self.assertIn(word, tokens)
        self.assertIn("no chest", tokens)
        self.assertIn("chest pain", tokens)
        _, _, state, _ = fit_features(self.cases[:7], self.cases[7:])
        self.assertEqual(state["kind"], "ems_tfidf_v2")
        self.assertEqual(state["parameters"]["stop_words"], COMMON_STOP_WORDS)
        self.assertTrue(all(not set(term.split()) & set(COMMON_STOP_WORDS) for term in state["vocabulary"]))

    def test_legacy_no_stopword_state_remains_transformable(self):
        import numpy as np
        import sklearn
        from sklearn.feature_extraction.text import TfidfVectorizer
        params = {**LEGACY_FIXED_PARAMS, "ngram_range": (1, 2), "max_features": 30}
        vectorizer = TfidfVectorizer(**params)
        vectorizer.fit([input_text(c) for c in self.cases[:7]])
        terms = vectorizer.get_feature_names_out().tolist()
        state = {"kind": "ems_tfidf_v1", "dataset_version": DATASET_VERSION,
                 "sklearn_version": sklearn.__version__, "parameters": {**params, "ngram_range": [1, 2]},
                 "input_recipe": "reported_demographics_then_earlier_then_latest_v1",
                 "input_fields": ["age_sex_weight", "prior_information", "current_transcript"],
                 "vocabulary": {term: i for i, term in enumerate(terms)}, "idf": vectorizer.idf_.tolist(),
                 "feature_columns": [f"tfidf_{i:04d}" for i in range(len(terms))],
                 "fit_case_ids": [c["case_id"] for c in self.cases[:7]]}
        state["state_sha256"] = canonical_hash(state)
        np.testing.assert_allclose(transform_records(self.cases[7:], state),
                                   vectorizer.transform([input_text(c) for c in self.cases[7:]]).toarray(),
                                   rtol=0, atol=1e-12)

    def fake_prepare(self, out, **kwargs):
        with patch("evals.tfidf_prep.import_workbook", return_value=copy.deepcopy(self.imported)):
            return prepare("artificial.xlsx", out, confirm_synthetic=True, acknowledge_provisional=True, **kwargs)

    def test_handoff_contains_only_training_table_dictionary_contract(self):
        with tempfile.TemporaryDirectory() as tmp, patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            out = Path(tmp) / "prep"
            audit = self.fake_prepare(out)
            self.assertEqual((audit["training_rows"], audit["test_rows"], audit["features"]), (7, 3, 30))
            share = out / "share_with_colleague"
            with (share / "train_features.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 7)
            self.assertEqual(set(rows[0]), {f"tfidf_{i:04d}" for i in range(30)} | {"esi_level"})
            self.assertTrue(all(0 <= float(row[f"tfidf_{i:04d}"]) <= 1 for row in rows for i in range(30)))
            with ZipFile(out / "colleague_handoff.zip") as archive:
                self.assertEqual(set(archive.namelist()), {"train_features.csv", "feature_dictionary.csv", "schema.json", "manifest.json", "README.md"})
                content = "\n".join(archive.read(name).decode() for name in archive.namelist())
                for secret in ("DOCTOR_ANSWER_SECRET", "EXPLANATION_SECRET", "ANNOTATION_SECRET", "REVIEWER_SECRET"):
                    self.assertNotIn(secret, content)
            local_sources = load_json(out / "private/source_inputs.json")
            self.assertEqual(local_sources["cases"], self.cases)
            private_import = load_json(out / "private/annotation_import.json")
            self.assertTrue(all(r["approved"] is False for r in private_import["references"]))
            with (out / "private/test_features.csv").open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 3)
            self.assertEqual(len(load_json(share / "manifest.json")["source_bundle_id"]), 64)
            before = (share / "train_features.csv").read_bytes()
            with self.assertRaises(ValueError):
                self.fake_prepare(out)
            self.assertEqual((share / "train_features.csv").read_bytes(), before)

    def test_missing_gates_and_import_issues_prevent_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "prep"
            with self.assertRaises(ValueError):
                prepare("not-read.xlsx", out)
            self.assertFalse(out.exists())
            self.imported["issues"].append({"case_id": "SYN001", "field": "esi_level", "problem": "needs review"})
            with self.assertRaisesRegex(ValueError, "Resolve import issues"):
                self.fake_prepare(out)
            self.assertFalse(out.exists())

    def test_feature_dictionary_csv_formula_text_is_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dictionary.csv"
            csv_file(path, [{"term": "=UNTRUSTED"}], ["term"])
            with path.open() as handle:
                self.assertEqual(next(csv.DictReader(handle))["term"], "'=UNTRUSTED")

    def test_transform_cli_uses_saved_state_without_refitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep = Path(tmp) / "prep"
            self.fake_prepare(prep)
            out = Path(tmp) / "transformed"
            result = subprocess.run([sys.executable, "-m", "evals.tfidf_prep", "transform", "--vectorizer",
                                     str(prep / "private/vectorizer.json"), "--cases", str(prep / "private/source_inputs.json"),
                                     "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no refit", result.stdout)
            self.assertTrue((out / "features.csv").exists())


if __name__ == "__main__":
    unittest.main()
