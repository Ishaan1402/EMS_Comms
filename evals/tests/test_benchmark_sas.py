"""Offline checks of the benchmark's SAS tables and fairness step: no SAS, no network."""
import asyncio
import csv
import io
import unittest

from backend import sas_sync
from evals.benchmark import sas_fairness, sas_model, sas_register, to_sas


def cas_table(columns, rows):
    return {"schema": [{"name": name} for name in columns], "rows": rows}


class FakeCas:
    """Answers freqTab and assessBias like CAS does, from canned counts."""

    def __init__(self, counts):
        self.counts = counts  # {(where, by, outcome): {group: {outcome value: frequency}}}
        self.settings = sas_sync.Settings(url='', client_id='', client_secret='')
        self.calls = []

    async def open_session(self):
        return "session"

    async def close_session(self, session):
        pass

    async def action(self, session, action, params):
        self.calls.append(action)
        if action == "freqTab.freqTab":
            by, outcome = params["tabulate"][0]["vars"][0], params["tabulate"][0]["cross"][0]
            counts = self.counts.get((params["table"]["where"], by, outcome), {})
            rows = [[group.ljust(12), value.ljust(10), n] for group, values in counts.items()
                    for value, n in values.items()]
            rows.append(["Female", "", 99])  # a total row: no outcome value, must be skipped
            return {"results": {"Table1.CrossList": cas_table([by, outcome, "Frequency"], rows)}}
        if action == "fairAITools.assessBias":
            return {"results": {
                "GroupMetrics": cas_table(["Group", "N", "TP", "FP", "TN", "FN", "TPR", "FPR", "FNR", "ACC"],
                                          [["Female", 10, 4, 3, 2, 1, 0.8, 0.6, 0.2, 0.6],
                                           ["Male", 10, 5, 3, 2, 0, 1.0, 0.6, 0.0, 0.7]]),
                "BiasMetrics": cas_table(["Metric", "MetricLabel", "Value", "Base", "Compare", "Note"],
                                         [["EqualOpportunity", "Equal Opportunity", 0.2, "Male", "Female", ""]]),
            }}
        return {"results": {}}


class BenchmarkSasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = to_sas.build_tables()

    def test_calls_table_has_every_call_and_no_free_text(self):
        calls = self.tables["CALLS"]
        self.assertEqual(sum(r["DATASET"] != "nhamcs" for r in calls), 3201)  # the v1 run
        header = next(csv.reader(io.StringIO(sas_sync.to_csv("CALLS", calls, to_sas.COLUMNS).decode())))
        self.assertEqual(header, [column for column, _, _ in to_sas.COLUMNS["CALLS"]])
        self.assertFalse({"ESI_REASON", "SUMMARY", "REPORT"} & set(header))

    def test_headline_matches_the_report(self):
        rows = {(r["SET_NAME"], r["RATER"], r["OUTCOME"]): r["VISITS"] for r in self.tables["HEADLINE"]}
        ktas = "KTAS ambulance arrivals"
        self.assertEqual(rows[(ktas, "Eir AI", "Given 1-2")], 138)
        self.assertEqual(rows[(ktas, "Eir AI", to_sas.NO_LEVEL)], 14)
        self.assertEqual(rows[(ktas, "Eir AI", "Given 3-5")], 2)
        self.assertEqual(rows[(ktas, "Triage nurses", "Given 3-5")], 15)
        self.assertNotIn(("MIMIC ambulance arrivals", "Triage nurses", "Given 3-5"), rows)

    def test_metrics_keep_numerator_and_denominator(self):
        row = next(r for r in self.tables["METRICS"] if r["ANALYSIS"] == "ktas/ems"
                   and r["METRIC"] == "reference_1_2_all_cases/given 1-2")
        self.assertEqual((row["NUMERATOR"], row["DENOMINATOR"]), (138, 154))

    def test_fairness_reads_sas_counts_and_checks_them(self):
        where = "PRIMARY = 1 and REFERENCE_URGENT = 1 and DATASET = 'ktas'"
        counts = {(where, "SEX", "URGENT_OUTCOME"): {"Female": {"Given 1-2": 8, "Given 3-5": 2},
                                                      "Male": {"Given 1-2": 9, to_sas.NO_LEVEL: 1}},
                  (where, "SEX", "NURSE_OUTCOME"): {"Female": {"Given 1-2": 9, "Given 3-5": 1}}}
        fake = FakeCas(counts)
        tables = asyncio.run(sas_fairness.analyse(fake))
        female = next(g for g in tables["BIAS_GROUPS"]
                      if g["SCOPE"] == "KTAS, all visits" and g["SENSITIVE_VARIABLE"] == "SEX"
                      and g["GROUP_VALUE"] == "Female")
        self.assertEqual((female["URGENT_VISITS"], female["GIVEN_3_5"], female["NURSE_GIVEN_3_5"]), (10, 2, 1))
        self.assertAlmostEqual(female["PLACED_LOWER_RATE"], 0.2)
        self.assertEqual(female["FN"], 1)
        self.assertIn("fairAITools.assessBias", fake.calls)
        metrics = {"esi": {key: {"reference_1_2_all_cases": {"given 1-2": {"k": 17}, "given 3-5": {"k": 2},
                                                             "no level": {"k": 1}}}
                           for key in ("ktas/all", "mimic/all")}}
        problems = sas_fairness.check_against_metrics(tables["BIAS_GROUPS"], metrics)
        self.assertTrue(any(p.startswith("KTAS, all visits by AGE_BAND") for p in problems))
        self.assertFalse(any(p.startswith("KTAS, all visits by SEX") for p in problems))

    def test_model_comparison_counts_the_holdout_and_matches_the_ai_false_flags(self):
        def visit(urgent, p, ai, gave=1, nurse=3, rule=3):
            return {"REFERENCE_URGENT": urgent, "P": p, "AI_URGENT": ai, "AI_GAVE_LEVEL": gave,
                    "NURSE_KTAS": nurse, "SIMPLE_RULE": rule}
        scored = [visit(1, 0.9, 1, nurse=2), visit(1, 0.3, None, gave=0), visit(1, 0.1, 0, rule=2),
                  visit(0, 0.6, 1), visit(0, 0.2, 0), visit(0, 0.05, 0)]
        rows = {r["MODEL"]: r for r in sas_model.compare(scored, "P")}
        ai = rows["Eir AI"]
        self.assertEqual((ai["URGENT_CAUGHT"], ai["URGENT_HELD"], ai["URGENT_MISSED"], ai["NOT_URGENT_FLAGGED"]),
                         (1, 1, 1, 1))
        default = rows["Gradient boosting (default cutoff)"]
        self.assertEqual((default["URGENT_CAUGHT"], default["NOT_URGENT_FLAGGED"]), (1, 1))
        matched = rows["Gradient boosting (tuned on holdout)"]
        self.assertEqual((matched["CUTOFF"], matched["URGENT_CAUGHT"], matched["NOT_URGENT_FLAGGED"]), (0.3, 2, 1))
        self.assertEqual(rows["Triage nurses"]["URGENT_CAUGHT"], 1)
        self.assertEqual(rows["Simple vitals rule"]["URGENT_CAUGHT"], 1)

    def test_model_card_and_property_names(self):
        import json
        metrics = json.loads((to_sas.RESULTS / "metrics.json").read_text(encoding="utf-8"))
        comparison = [{"MODEL": "Eir AI", "READS": "the crew's report", "URGENT_CAUGHT": 63, "URGENT_HELD": 9,
                       "URGENT_MISSED": 2, "NOT_URGENT_FLAGGED": 77, "HOLDOUT_NOT_URGENT": 306}]
        card = sas_register.model_card(metrics, self.tables, comparison)
        self.assertIn("Eir put 138 at 1-2", card)
        self.assertIn("| Eir AI | the crew's report | 63 | 9 | 2 | 77 of 306 |", card)
        self.assertIn("KTAS by sex, put at 3-5: Female 5/100, Male 2/146", card)
        self.assertIn("KTAS by age band, no level: 65 and over 8/124, Under 65 23/122 (Fisher p 0.004, significant", card)
        for prop in sas_register.properties(metrics, self.tables):
            self.assertRegex(prop["name"], r"^[A-Za-z0-9 _.-]+$")  # what Model Manager accepts

    def test_outcomes_count_each_rater_by_what_happened_to_the_patient(self):
        rows = {(r["SCOPE"], r["OUTCOME_GROUP"], r["RATER"], r["PLACED"]): r for r in self.tables["OUTCOMES"]}
        critical = ("KTAS all visits", "ICU, death or surgery from ED")
        self.assertEqual(rows[critical + ("Eir AI", "Given 1-2")]["VISITS"], 22)
        self.assertEqual(rows[critical + ("Eir AI", to_sas.NO_LEVEL)]["VISITS"], 13)
        self.assertEqual(rows[critical + ("KTAS experts", "Given 1-2")]["GROUP_VISITS"], 39)
        self.assertEqual(rows[critical + ("Triage nurses", "Given 3-5")]["VISITS"], 19)
        self.assertNotIn(critical + ("Triage nurses", to_sas.NO_LEVEL), rows)


if __name__ == "__main__":
    unittest.main()
