"""Check the AI for bias by sex and age band with SAS's own tools, in CAS, and save the results there.

    python -m evals.benchmark.to_sas push       # first: EIR_BENCH_CALLS must be in the caslib
    python -m evals.benchmark.sas_fairness      # runs in CAS, writes EIR_BENCH_BIAS_GROUPS, _TESTS, _METRICS

Two CAS actions do the work, through the app's SAS client:
- freqTab.freqTab counts, for every visit whose reference level is 1-2 (the urgent ones), how many
  the AI put at 1-2, at 3-5 or gave no level (Cannot assess, shown as Needs review), per group; and
  on the KTAS set the same for the triage nurses, as a human yardstick.
- fairAITools.assessBias compares the groups on the visits the AI gave a level for: "urgent" is
  reference 1-2, the AI's prediction is "gave 1-2". It reports demographic parity, equal
  opportunity (the gap in the share of urgent visits flagged urgent) and equalized odds.

EIR_BENCH_BIAS_TESTS compares the two groups of each variable within each dataset, for both ways an
urgent patient can be let down: put at 3-5, and given no level (held for review). Each is a two-sided
Fisher's exact test (stats.fisher_exact). KTAS and MIMIC (protocol v1: 8 tests) and NHAMCS (v1.1:
sex, age band, and each race/ethnicity group against non-Hispanic White) are separate families, each
with its own Bonferroni bar. The datasets use different scales, so they are not pooled.

The counts are SAS's; the 95% Wilson intervals (stats.wilson) and the Fisher tests are computed here
from those counts. Primary results only (repeat 1), as in REPORT.md.
"""
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

from backend import sas_sync
from .stats import fisher_exact, wilson
from .to_sas import NO_LEVEL, PREFIX, RESULTS

SCOPES = {
    "KTAS, all visits": "DATASET = 'ktas'",
    "MIMIC, all stays": "DATASET = 'mimic'",
    "KTAS and MIMIC": "DATASET in ('ktas', 'mimic')",
    "NHAMCS, all visits": "DATASET = 'nhamcs'",
}
SENSITIVE = ("SEX", "AGE_BAND", "RACE_ETHNICITY")
# Race/ethnicity is recorded only in NHAMCS; each group is compared with this one.
REFERENCE_GROUP = {"RACE_ETHNICITY": "Non-Hispanic White"}
# Tests run within one dataset only (the references are different scales), in two families with
# their own Bonferroni bar: protocol v1 (KTAS, MIMIC) and its v1.1 addendum (NHAMCS).
TEST_FAMILIES = {"KTAS, all visits": "v1", "MIMIC, all stays": "v1", "NHAMCS, all visits": "v1.1"}
TESTED_OUTCOMES = {"GIVEN_3_5": "Put at 3-5", "NO_LEVEL": "No level (Needs review)"}
OUTCOMES = {"Given 1-2": "GIVEN_1_2", "Given 3-5": "GIVEN_3_5", NO_LEVEL: "NO_LEVEL"}
# The nurses' KTAS for the same urgent visits (KTAS set only), as text so freqTab can cross it.
NURSE_PROGRAM = "length NURSE_OUTCOME $ 10; if NURSE_KTAS <= 2 then NURSE_OUTCOME = 'Given 1-2'; " \
                "else NURSE_OUTCOME = 'Given 3-5';"
BIAS_PROGRAM = "P_URGENT = AI_URGENT; P_NOT_URGENT = 1 - AI_URGENT; length URGENT $ 3; " \
               "URGENT = ifc(REFERENCE_URGENT = 1, 'Yes', 'No');"
GROUP_METRICS = ("N", "TP", "FP", "TN", "FN", "TPR", "FPR", "FNR", "ACC")

COLUMNS = {
    "BIAS_GROUPS": [
        ("SCOPE", "char", "Visits analysed"),
        ("SENSITIVE_VARIABLE", "char", "Grouped by"),
        ("GROUP_VALUE", "char", "Group"),
        ("URGENT_VISITS", "num", "Visits with reference level 1-2"),
        ("GIVEN_1_2", "num", "AI put them at 1-2"),
        ("GIVEN_3_5", "num", "AI put them at 3-5"),
        ("NO_LEVEL", "num", "AI gave no level (Needs review)"),
        ("PLACED_LOWER_RATE", "num", "Share of urgent visits the AI put at 3-5"),
        ("PLACED_LOWER_LOW", "num", "95% interval, low"),
        ("PLACED_LOWER_HIGH", "num", "95% interval, high"),
        ("NO_LEVEL_RATE", "num", "Share of urgent visits with no AI level"),
        ("NURSE_GIVEN_3_5", "num", "Triage nurses put them at KTAS 3-5 (KTAS only)"),
        ("NURSE_PLACED_LOWER_RATE", "num", "Share the nurses put at 3-5 (KTAS only)"),
        ("ASSESSED_N", "num", "Visits the AI gave a level for (assessBias)"),
        ("TP", "num", "Urgent, AI gave 1-2"),
        ("FP", "num", "Not urgent, AI gave 1-2"),
        ("TN", "num", "Not urgent, AI gave 3-5"),
        ("FN", "num", "Urgent, AI gave 3-5"),
        ("TPR", "num", "Urgent visits the AI flagged urgent (true positive rate)"),
        ("FPR", "num", "Non-urgent visits the AI flagged urgent (false positive rate)"),
        ("FNR", "num", "Urgent visits the AI put at 3-5 (false negative rate)"),
        ("ACC", "num", "Urgent / not urgent accuracy"),
    ],
    "BIAS_TESTS": [
        ("SCOPE", "char", "Visits analysed"),
        ("SENSITIVE_VARIABLE", "char", "Grouped by"),
        ("OUTCOME", "char", "What happened to the urgent visits"),
        ("GROUP_A", "char", "First group"),
        ("A_EVENTS", "num", "First group: urgent visits with this outcome"),
        ("A_URGENT", "num", "First group: urgent visits"),
        ("A_RATE", "num", "First group: share"),
        ("GROUP_B", "char", "Second group"),
        ("B_EVENTS", "num", "Second group: urgent visits with this outcome"),
        ("B_URGENT", "num", "Second group: urgent visits"),
        ("B_RATE", "num", "Second group: share"),
        ("FISHER_P", "num", "Two-sided Fisher's exact p"),
        ("TESTS_RUN", "num", "Tests run in this family"),
        ("SIGNIFICANT", "num", "p below 0.05 / tests run (1/0)"),
    ],
    "BIAS_METRICS": [
        ("SCOPE", "char", "Visits analysed"),
        ("SENSITIVE_VARIABLE", "char", "Grouped by"),
        ("METRIC", "char", "Bias metric"),
        ("METRIC_LABEL", "char", "Bias metric (label)"),
        ("VALUE", "num", "Largest difference between groups"),
        ("BASE_GROUP", "char", "Group with the higher value"),
        ("COMPARE_GROUP", "char", "Group with the lower value"),
        ("NOTE", "char", "SAS note"),
    ],
}


def _rows(table: dict) -> list:
    names = [column["name"] for column in table["schema"]]
    return [dict(zip(names, row)) for row in table["rows"]]


def _crosslist(results: dict) -> list:
    return [row for key, table in results.items() if key.endswith("CrossList") for row in _rows(table)]


async def _counts(client, session, where: str, by: str, outcome: str, program: str = "") -> dict:
    """{group: {outcome: frequency}} for the urgent visits in `where`, from freqTab."""
    table = {"caslib": client.settings.caslib, "name": PREFIX + "CALLS",
             "where": f"PRIMARY = 1 and REFERENCE_URGENT = 1 and {where}"}
    if program:
        table |= {"computedVars": [{"name": outcome}], "computedVarsProgram": program}
    result = await client.action(session, "freqTab.freqTab", {
        "table": table, "tabulate": [{"vars": [by], "cross": [outcome]}], "printLevels": False})
    counts = {}
    for row in _crosslist(result["results"]):
        group, value = (row[by] or "").strip(), (row[outcome] or "").strip()  # fixed-length text comes padded
        if group and value:
            counts.setdefault(group, {})[value] = int(row["Frequency"])
    return counts


async def analyse(client) -> dict:
    groups, metrics = [], []
    session = await client.open_session()
    try:
        for action_set in ("freqTab", "fairAITools"):
            await client.action(session, "builtins.loadActionSet", {"actionSet": action_set})
        for scope, where in SCOPES.items():
            for by in SENSITIVE:
                if by == "RACE_ETHNICITY" and not scope.startswith("NHAMCS"):
                    continue  # recorded only in NHAMCS
                ai = await _counts(client, session, where, by, "URGENT_OUTCOME")
                nurse = (await _counts(client, session, where, by, "NURSE_OUTCOME", NURSE_PROGRAM)
                         if scope.startswith("KTAS,") else {})
                bias = (await client.action(session, "fairAITools.assessBias", {
                    "table": {"caslib": client.settings.caslib, "name": PREFIX + "CALLS",
                              "where": f"PRIMARY = 1 and AI_GAVE_LEVEL = 1 and {where}",
                              "computedVars": [{"name": "P_URGENT"}, {"name": "P_NOT_URGENT"}, {"name": "URGENT"}],
                              "computedVarsProgram": BIAS_PROGRAM},
                    "modelTableType": "NONE", "response": {"name": "URGENT"}, "event": "Yes",
                    "responseLevels": ["Yes", "No"],
                    "predictedVariables": [{"name": "P_URGENT"}, {"name": "P_NOT_URGENT"}],
                    "sensitiveVariable": {"name": by},
                }))["results"]
                by_group = {row["Group"]: row for row in _rows(bias["GroupMetrics"])}
                for group in sorted(ai):
                    outcome = {column: ai[group].get(label, 0) for label, column in OUTCOMES.items()}
                    urgent = sum(outcome.values())
                    lower = wilson(outcome["GIVEN_3_5"], urgent)
                    nurse_counts = nurse.get(group)
                    nurse_lower = nurse_counts.get("Given 3-5", 0) if nurse_counts else None
                    fair = by_group.get(group, {})
                    groups.append({
                        "SCOPE": scope, "SENSITIVE_VARIABLE": by, "GROUP_VALUE": group, "URGENT_VISITS": urgent,
                        **outcome,
                        "PLACED_LOWER_RATE": lower["value"], "PLACED_LOWER_LOW": lower["low"],
                        "PLACED_LOWER_HIGH": lower["high"],
                        "NO_LEVEL_RATE": outcome["NO_LEVEL"] / urgent if urgent else None,
                        "NURSE_GIVEN_3_5": nurse_lower,
                        "NURSE_PLACED_LOWER_RATE": nurse_lower / sum(nurse_counts.values()) if nurse_counts else None,
                        "ASSESSED_N": fair.get("N"), **{key: fair.get(key) for key in GROUP_METRICS[1:]},
                    })
                for row in _rows(bias["BiasMetrics"]):
                    metrics.append({"SCOPE": scope, "SENSITIVE_VARIABLE": by, "METRIC": row["Metric"],
                                    "METRIC_LABEL": row["MetricLabel"], "VALUE": row["Value"],
                                    "BASE_GROUP": row["Base"], "COMPARE_GROUP": row["Compare"],
                                    "NOTE": row["Note"] or None})
    finally:
        try:
            await client.close_session(session)
        except sas_sync.SasError as error:  # the session times out on its own
            print(f"SAS: could not close CAS session: {error}")
    return {"BIAS_GROUPS": groups, "BIAS_TESTS": tests(groups), "BIAS_METRICS": metrics}


def tests(groups: list) -> list:
    """
    Fisher's exact test per dataset, variable and outcome: the two groups against each other, or
    (race/ethnicity) every group against REFERENCE_GROUP. SIGNIFICANT uses 0.05 / the family's tests.
    """
    rows = []
    for scope in TEST_FAMILIES:
        for by in SENSITIVE:
            found = sorted((g for g in groups if g["SCOPE"] == scope and g["SENSITIVE_VARIABLE"] == by),
                           key=lambda g: g["GROUP_VALUE"])
            base = next((g for g in found if g["GROUP_VALUE"] == REFERENCE_GROUP.get(by)), None)
            if base:
                pairs = [(base, g) for g in found if g is not base]
            elif len(found) == 2:
                pairs = [tuple(found)]
            else:
                continue
            for a, b in pairs:
                for column, outcome in TESTED_OUTCOMES.items():
                    p = fisher_exact(a[column], a["URGENT_VISITS"], b[column], b["URGENT_VISITS"])
                    rows.append({"SCOPE": scope, "SENSITIVE_VARIABLE": by, "OUTCOME": outcome,
                                 "GROUP_A": a["GROUP_VALUE"], "A_EVENTS": a[column], "A_URGENT": a["URGENT_VISITS"],
                                 "A_RATE": round(a[column] / a["URGENT_VISITS"], 4),
                                 "GROUP_B": b["GROUP_VALUE"], "B_EVENTS": b[column], "B_URGENT": b["URGENT_VISITS"],
                                 "B_RATE": round(b[column] / b["URGENT_VISITS"], 4), "FISHER_P": round(p, 4)})
    for row in rows:
        family = [r for r in rows if TEST_FAMILIES[r["SCOPE"]] == TEST_FAMILIES[row["SCOPE"]]]
        row["TESTS_RUN"] = len(family)
        row["SIGNIFICANT"] = int(row["FISHER_P"] < 0.05 / len(family))
    return rows


def check_against_metrics(groups: list, metrics: dict) -> list:
    """SAS's totals per scope must equal score.py's reference 1-2 counts; returns the mismatches."""
    problems = []
    for scope, key in (("KTAS, all visits", "ktas/all"), ("MIMIC, all stays", "mimic/all"),
                       ("NHAMCS, all visits", "nhamcs/all")):
        if key not in metrics["esi"]:
            continue
        expected = metrics["esi"][key]["reference_1_2_all_cases"]
        for by in SENSITIVE:
            if by == "RACE_ETHNICITY" and not scope.startswith("NHAMCS"):
                continue
            rows = [g for g in groups if g["SCOPE"] == scope and g["SENSITIVE_VARIABLE"] == by]
            for label, column in OUTCOMES.items():
                got = sum(g[column] for g in rows)
                want = expected[{"Given 1-2": "given 1-2", "Given 3-5": "given 3-5", NO_LEVEL: "no level"}[label]]["k"]
                if got != want:
                    problems.append(f"{scope} by {by}, {label}: SAS {got}, Python {want}")
    return problems


def main(argv=None) -> int:
    settings = dataclasses.replace(sas_sync.Settings.from_env(), prefix=PREFIX)
    if not settings.configured:
        print("Set SAS_VIYA_URL and SAS_CLIENT_ID + SAS_CLIENT_SECRET (or SAS_ACCESS_TOKEN) in .env")
        return 1

    async def run():
        client = sas_sync.CasClient(settings)
        try:
            tables = await analyse(client)
            await client.replace_tables({name: sas_sync.to_csv(name, rows, COLUMNS) for name, rows in tables.items()},
                                        COLUMNS)
            return tables
        finally:
            await client.close()
    try:
        tables = asyncio.run(run())
    except sas_sync.SasError as error:
        print(f"SAS: {error}")
        return 1
    for name, rows in tables.items():
        print(f"{settings.caslib}.{PREFIX}{name}: {len(rows)} rows")
    for g in tables["BIAS_GROUPS"]:
        nurse = f", nurses {g['NURSE_GIVEN_3_5']}/{g['URGENT_VISITS']}" if g["NURSE_GIVEN_3_5"] is not None else ""
        print(f"  {g['SCOPE']:17} {g['GROUP_VALUE']:12} urgent {g['URGENT_VISITS']:3}: AI put {g['GIVEN_3_5']} at 3-5 "
              f"({g['PLACED_LOWER_RATE']:.1%}, {g['PLACED_LOWER_LOW']:.1%}-{g['PLACED_LOWER_HIGH']:.1%}), "
              f"no level {g['NO_LEVEL']}{nurse}; flagged urgent {g['TPR']:.1%} of urgent it rated")
    for t in tables["BIAS_TESTS"]:
        print(f"  {t['SCOPE']:17} {t['OUTCOME']:24} {t['GROUP_A']} {t['A_EVENTS']}/{t['A_URGENT']} vs "
              f"{t['GROUP_B']} {t['B_EVENTS']}/{t['B_URGENT']}: Fisher p {t['FISHER_P']:.4f}"
              f"{'  SIGNIFICANT after ' + str(t['TESTS_RUN']) + ' tests' if t['SIGNIFICANT'] else ''}")
    problems = check_against_metrics(tables["BIAS_GROUPS"],
                                     json.loads((RESULTS / "metrics.json").read_text(encoding="utf-8")))
    print("SAS counts match metrics.json" if not problems else "MISMATCH:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    sys.exit(main())
