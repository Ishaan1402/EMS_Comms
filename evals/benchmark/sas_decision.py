"""Inputs for two SAS steps built in the UI: a safety-net decision (Intelligent Decisioning) and
performance monitoring of the AI (Model Manager). Run after to_sas push.

    python -m evals.benchmark.sas_decision push     # EIR_DECISION_INPUT + EIRPERF_1_KTAS / _2_MIMIC / _3_NHAMCS
    python -m evals.benchmark.sas_decision check     # compares EIR_DECISION_RESULTS (from the ID test) with RULE

The decision, in plain words (RULE below is the reference implementation the SAS one must match):
use the AI's level when it gives one; when it says Cannot assess, flag the patient urgent if any
vital sign it can see is in the danger zone, else leave it for clinician review. A missing vital is
never in the danger zone. The thresholds are ours (score.rule_baseline), not a clinical tool.

The monitoring batches hold the visits the AI gave a level for, one table per benchmark set, with
the target (reference 1-2), the AI's urgent flag as its event probability, and sex and age band for
Model Manager's bias monitoring. Free text never goes to SAS.
"""
import asyncio
import dataclasses
import sys
from collections import Counter
from pathlib import Path

from backend import sas_sync
from .to_sas import PREFIX, build_tables

MENTAL_CODE = {"Alert": 1, "Responds to voice": 2, "Responds to pain": 3, "Unresponsive": 4}
DECISIONS = ("Urgent: AI level 1-2", "Not urgent: AI level 3-5",
             "Urgent: safety net (no AI level, danger-zone vitals)", "Needs review: no AI level")
SCOPES = {"KTAS ambulance arrivals": lambda r: r["DATASET"] == "ktas" and r["EMS_ARRIVAL"],
          "MIMIC ambulance arrivals": lambda r: r["DATASET"] == "mimic" and r["EMS_ARRIVAL"],
          "NHAMCS ambulance arrivals": lambda r: r["DATASET"] == "nhamcs"}
BATCHES = (("EIRPERF_1_KTAS", "ktas"), ("EIRPERF_2_MIMIC", "mimic"), ("EIRPERF_3_NHAMCS", "nhamcs"))

COLUMNS = {
    "DECISION_INPUT": [
        ("CASE_ID", "char", "Case"), ("DATASET", "char", "Benchmark set"),
        ("EMS_ARRIVAL", "num", "Ambulance arrival (1/0)"),
        ("REFERENCE_URGENT", "num", "Reference level 1-2 (1/0)"), ("OUTCOME", "char", "Outcome (NHAMCS only)"),
        ("AI_GAVE_LEVEL", "num", "AI gave a level (1/0)"), ("AI_ESI", "num", "AI's ESI level"),
        ("MENTAL_CODE", "num", "Mental state: 1 alert, 2 voice, 3 pain, 4 unresponsive (KTAS only)"),
        ("HR", "num", "Heart rate"), ("SBP", "num", "Systolic BP"), ("RR", "num", "Respiratory rate"),
        ("SPO2", "num", "SpO2"),
    ],
    "PERF": [
        ("CASE_ID", "char", "Case"), ("REFERENCE_URGENT", "num", "Reference level 1-2 (target, 1 = urgent)"),
        ("AI_URGENT", "num", "AI gave level 1-2 (event probability, 0/1)"),
        ("SEX", "char", "Sex"), ("AGE_BAND", "char", "Age band"),
    ],
}


def danger_zone(r) -> bool:
    def above(key, limit):
        return r[key] is not None and r[key] > limit

    def below(key, limit):
        return r[key] is not None and r[key] < limit
    return ((r["MENTAL_CODE"] or 0) >= 2 or above("HR", 100) or above("RR", 20) or below("SPO2", 92)
            or below("SBP", 90))


def RULE(r) -> str:
    if r["AI_GAVE_LEVEL"]:
        return DECISIONS[0] if r["AI_ESI"] <= 2 else DECISIONS[1]
    return DECISIONS[2] if danger_zone(r) else DECISIONS[3]


def decision_rows(calls: list) -> list:
    return [{"CASE_ID": r["CASE_ID"], "DATASET": r["DATASET"], "EMS_ARRIVAL": r["EMS_ARRIVAL"],
             "REFERENCE_URGENT": r["REFERENCE_URGENT"], "OUTCOME": r["OUTCOME"], "AI_GAVE_LEVEL": r["AI_GAVE_LEVEL"],
             "AI_ESI": r["AI_ESI"], "MENTAL_CODE": MENTAL_CODE.get(r["MENTAL_STATE"]), "HR": r["HR"], "SBP": r["SBP"],
             "RR": r["RR"], "SPO2": r["SPO2"]}
            for r in calls if r["PRIMARY"] and r["DATASET"] in ("ktas", "mimic", "nhamcs")]


def expected(rows: list) -> dict:
    """{scope: {(urgent?, decision): visits}} from RULE: the numbers the SAS decision must reproduce."""
    out = {}
    for scope, chosen in SCOPES.items():
        out[scope] = Counter((r["REFERENCE_URGENT"], RULE(r)) for r in rows if chosen(r))
    return out


def summary_lines(counts: dict) -> list:
    lines = []
    for scope, c in counts.items():
        urgent = {d: c[(1, d)] for d in DECISIONS}
        calm = {d: c[(0, d)] for d in DECISIONS}
        lines.append(f"{scope}: urgent visits " + ", ".join(f"{d} {n}" for d, n in urgent.items())
                     + " | non-urgent " + ", ".join(f"{d} {n}" for d, n in calm.items()))
    return lines


async def push(client, tables: dict) -> None:
    await client.replace_tables({name: sas_sync.to_csv(spec, rows, COLUMNS) for name, (spec, rows) in tables.items()},
                                {name: COLUMNS[spec] for name, (spec, _) in tables.items()})


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    command = argv[0] if argv else "push"
    calls = build_tables()["CALLS"]
    rows = decision_rows(calls)
    if command == "push":
        tables = {"DECISION_INPUT": ("DECISION_INPUT", rows)}
        for name, dataset in BATCHES:
            tables[name] = ("PERF", [{"CASE_ID": r["CASE_ID"], "REFERENCE_URGENT": r["REFERENCE_URGENT"],
                                      "AI_URGENT": r["AI_URGENT"], "SEX": r["SEX"], "AGE_BAND": r["AGE_BAND"]}
                                     for r in calls if r["PRIMARY"] and r["DATASET"] == dataset and r["AI_GAVE_LEVEL"]])
        # Monitoring tables keep their own names (Model Manager's prefix_N_label); the input gets the EIR_ prefix.
        settings = sas_sync.Settings.from_env()

        async def run():
            for prefix, names in (("EIR_", ["DECISION_INPUT"]), ("", [n for n, _ in BATCHES])):
                client = sas_sync.CasClient(dataclasses.replace(settings, prefix=prefix))
                try:
                    await push(client, {n: tables[n] for n in names})
                finally:
                    await client.close()
        asyncio.run(run())
        print(f"{settings.caslib}.EIR_DECISION_INPUT: {len(rows)} rows")
        for name, _ in BATCHES:
            print(f"{settings.caslib}.{name}: {len(tables[name][1])} rows")
        print("Expected decision results (RULE):")
        print("\n".join("  " + line for line in summary_lines(expected(rows))))
        return 0
    if command == "check":
        return check(rows)
    print("usage: python -m evals.benchmark.sas_decision push|check")
    return 2


def check(rows: list) -> int:
    """Every case's DECISION in EIR_DECISION_RESULTS (written by the Intelligent Decisioning test) equals RULE."""
    settings = sas_sync.Settings.from_env()

    async def fetch():
        client = sas_sync.CasClient(settings)
        session = await client.open_session()
        try:
            found = await client.action(session, "table.tableExists", {"caslib": settings.caslib, "name": "EIR_DECISION_RESULTS"})
            if not found["results"]["exists"]:
                return None
            result = await client.action(session, "table.fetch", {
                "table": {"caslib": settings.caslib, "name": "EIR_DECISION_RESULTS"}, "to": 100000, "maxRows": 100000})
            table = result["results"]["Fetch"]
            names = [c["name"] for c in table["schema"]]
            return [dict(zip(names, row)) for row in table["rows"]]
        finally:
            await client.close_session(session)
            await client.close()
    got = asyncio.run(fetch())
    if got is None:
        print("EIR_DECISION_RESULTS is not in the caslib yet")
        return 1
    column = next((c for c in got[0] if c.upper() == "DECISION"), None) if got else None
    by_case = {r["CASE_ID"].strip(): (r.get(column) or "").strip() for r in got} if column else {}
    wrong = [(r["CASE_ID"], RULE(r), by_case.get(r["CASE_ID"])) for r in rows if by_case.get(r["CASE_ID"]) != RULE(r)]
    print(f"{len(got)} result rows, DECISION column {column!r}; {len(rows) - len(wrong)} of {len(rows)} match RULE")
    for case in wrong[:10]:
        print("  differs:", case)
    return 0 if not wrong else 1


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    sys.exit(main())
