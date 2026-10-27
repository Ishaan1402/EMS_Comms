"""A SAS baseline model next to the AI: gradient boosting on the structured triage fields, in CAS.

    python -m evals.benchmark.to_sas push       # first: EIR_BENCH_CALLS must be in the caslib
    python -m evals.benchmark.sas_model         # writes EIR_BENCH_MODEL_COMPARISON (and EIR_BENCH_FEATURES
                                                # if missing; --rebuild-features draws it again)

The question it answers: could a model on the numbers alone (age, sex, arrival, mental state and
the six vitals) find the urgent patients (expert KTAS 1-2) as well as the AI reading the report?

- fedSql.execDirect builds the KTAS visits (primary result) from EIR_BENCH_CALLS, and
  sampling.stratified holds out 30% by target with a fixed seed (_PartInd_ = 1). The table is
  saved as EIR_BENCH_FEATURES, so Model Studio can use the same holdout (partition variable).
- decisionTree.gbtreeTrain / gbtreeScore fit 100 trees on the 70% and score the holdout.
- EIR_BENCH_MODEL_COMPARISON counts, on the holdout only, how many urgent visits each one
  caught, held for review (the AI's "Cannot assess") or missed, and how many non-urgent visits it
  flagged: the AI, the model at its default cutoff (0.5), the model at the cutoff that flags no
  more non-urgent holdout visits than the AI does, the triage nurses and the simple vitals rule.
  That second cutoff is tuned on the holdout itself, which favours the model: it is a like-for-like
  false-alarm comparison, not an unbiased estimate of how the model would do on new visits.

The AI's columns are kept in the table for the comparison and must stay rejected as inputs.
"""
import asyncio
import dataclasses
import sys
from pathlib import Path

from backend import sas_sync
from .to_sas import PREFIX

SEED = 20261026
HOLDOUT_PERCENT = 30
INPUTS = ("AGE", "SEX", "EMS_ARRIVAL", "MENTAL_STATE", "HR", "SBP", "DBP", "RR", "SPO2", "TEMP_C")
NOMINALS = ("SEX", "EMS_ARRIVAL", "MENTAL_STATE")
# PRIMARY is an SQL keyword, so it is quoted.
FEATURES_QUERY = """create table {caslib}.""" + PREFIX + """FEATURES_SOURCE {{options replace=true}} as
    select CASE_ID, REFERENCE_LEVEL, REFERENCE_URGENT, """ + ", ".join(INPUTS) + """,
           NURSE_KTAS, SIMPLE_RULE, AI_GAVE_LEVEL, AI_ESI, AI_URGENT
    from {caslib}.""" + PREFIX + """CALLS where DATASET = 'ktas' and "PRIMARY" = 1"""

COLUMNS = {
    "MODEL_COMPARISON": [
        ("MODEL", "char", "Who placed the holdout visits"),
        ("READS", "char", "What it reads"),
        ("CUTOFF", "num", "Probability cutoff (models only)"),
        ("HOLDOUT_URGENT", "num", "Urgent visits in the holdout (expert KTAS 1-2)"),
        ("URGENT_CAUGHT", "num", "Urgent visits put at 1-2 (or above the cutoff)"),
        ("URGENT_HELD", "num", "Urgent visits held for review (AI: Cannot assess)"),
        ("URGENT_MISSED", "num", "Urgent visits put at 3-5 (or below the cutoff)"),
        ("HOLDOUT_NOT_URGENT", "num", "Non-urgent visits in the holdout"),
        ("NOT_URGENT_FLAGGED", "num", "Non-urgent visits put at 1-2 (or above the cutoff)"),
        ("CAUGHT_RATE", "num", "Share of urgent visits caught"),
        ("MISSED_RATE", "num", "Share of urgent visits missed"),
    ],
}


def _rows(table: dict) -> list:
    names = [column["name"] for column in table["schema"]]
    return [dict(zip(names, row)) for row in table["rows"]]


def _row(model, reads, urgent, not_urgent, caught, held, flagged, cutoff=None) -> dict:
    missed = len(urgent) - caught - held
    return {"MODEL": model, "READS": reads, "CUTOFF": cutoff, "HOLDOUT_URGENT": len(urgent),
            "URGENT_CAUGHT": caught, "URGENT_HELD": held, "URGENT_MISSED": missed,
            "HOLDOUT_NOT_URGENT": len(not_urgent), "NOT_URGENT_FLAGGED": flagged,
            "CAUGHT_RATE": round(caught / len(urgent), 4), "MISSED_RATE": round(missed / len(urgent), 4)}


def compare(scored: list, probability: str) -> list:
    """The comparison rows from the scored holdout (one dict per visit)."""
    urgent = [r for r in scored if r["REFERENCE_URGENT"] == 1]
    not_urgent = [r for r in scored if r["REFERENCE_URGENT"] == 0]
    ai_flagged = sum(r["AI_URGENT"] == 1 for r in not_urgent)
    rows = [_row("Eir AI", "the crew's report", urgent, not_urgent,
                 sum(r["AI_URGENT"] == 1 for r in urgent), sum(r["AI_GAVE_LEVEL"] == 0 for r in urgent), ai_flagged)]

    def model_row(cutoff, label):
        return _row(label, "age, sex, arrival, mental state, 6 vitals", urgent, not_urgent,
                    sum(r[probability] >= cutoff for r in urgent), 0,
                    sum(r[probability] >= cutoff for r in not_urgent), cutoff)
    rows.append(model_row(0.5, "SAS gradient boosting, default cutoff"))
    # The lowest cutoff (most urgent visits caught) that flags no more non-urgent visits than the AI.
    candidates = sorted({round(r[probability], 6) for r in scored})
    matched = min((c for c in candidates if sum(r[probability] >= c for r in not_urgent) <= ai_flagged),
                  default=1.0)
    rows.append(model_row(matched, "SAS gradient boosting, cutoff tuned on the holdout to Eir's false flags"))
    rows.append(_row("Triage nurses", "the patient, at triage", urgent, not_urgent,
                     sum(r["NURSE_KTAS"] <= 2 for r in urgent), 0, sum(r["NURSE_KTAS"] <= 2 for r in not_urgent)))
    rows.append(_row("Simple vitals rule", "mental state and 5 vitals", urgent, not_urgent,
                     sum(r["SIMPLE_RULE"] <= 2 for r in urgent), 0, sum(r["SIMPLE_RULE"] <= 2 for r in not_urgent)))
    return rows


async def _build_features(client, session, caslib: str, features: str) -> None:
    await client.action(session, "fedSql.execDirect", {"query": FEATURES_QUERY.format(caslib=caslib)})
    await client.action(session, "sampling.stratified", {
        "table": {"caslib": caslib, "name": PREFIX + "FEATURES_SOURCE", "groupBy": ["REFERENCE_URGENT"]},
        "samppct": HOLDOUT_PERCENT, "seed": SEED, "partInd": True,
        "output": {"casOut": {"caslib": caslib, "name": features + "_STAGE", "replace": True},
                   "copyVars": "ALL"}})
    await client.action(session, "table.dropTable", {"caslib": caslib, "name": features, "quiet": True})
    await client.action(session, "table.promote", {"caslib": caslib, "name": features + "_STAGE",
                                                   "target": features, "targetLib": caslib, "drop": True})
    await client.action(session, "table.save", {"table": {"caslib": caslib, "name": features},
                                                "caslib": caslib, "name": f"{features}.sashdat", "replace": True})


async def _features_ready(client, session, caslib: str, features: str) -> bool:
    """The shared table, loaded back from its saved copy after a CAS restart if needed."""
    found = await client.action(session, "table.tableExists", {"caslib": caslib, "name": features})
    if found["results"]["exists"]:
        return True
    try:
        await client.action(session, "table.loadTable", {"caslib": caslib, "path": f"{features}.sashdat",
                                                         "casOut": {"caslib": caslib, "name": features, "promote": True}})
        return True
    except sas_sync.SasError:
        return False


async def analyse(client, rebuild: bool = False) -> list:
    """
    Keeps an existing EIR_BENCH_FEATURES unless rebuild: Model Studio projects are trained on its
    _PartInd_, and CAS row order can differ between runs, so rebuilding may draw a different holdout.
    """
    features, caslib = PREFIX + "FEATURES", client.settings.caslib
    session = await client.open_session()
    try:
        for action_set in ("fedSql", "sampling", "decisionTree"):
            await client.action(session, "builtins.loadActionSet", {"actionSet": action_set})
        if rebuild or not await _features_ready(client, session, caslib, features):
            await _build_features(client, session, caslib, features)
        await client.action(session, "decisionTree.gbtreeTrain", {
            "table": {"caslib": caslib, "name": features, "where": "_PartInd_ = 0"},
            "target": "REFERENCE_URGENT", "inputs": list(INPUTS), "nominals": ["REFERENCE_URGENT", *NOMINALS],
            "nTree": 100, "seed": SEED, "casOut": {"caslib": caslib, "name": "EIR_BENCH_GB_SESSION", "replace": True}})
        await client.action(session, "decisionTree.gbtreeScore", {
            "table": {"caslib": caslib, "name": features, "where": "_PartInd_ = 1"},
            "modelTable": {"caslib": caslib, "name": "EIR_BENCH_GB_SESSION"},
            "casOut": {"caslib": caslib, "name": "EIR_BENCH_SCORED_SESSION", "replace": True},
            "copyVars": ["CASE_ID", "REFERENCE_URGENT", "AI_URGENT", "AI_GAVE_LEVEL", "NURSE_KTAS", "SIMPLE_RULE"],
            "encodeName": True})
        fetched = await client.action(session, "table.fetch", {
            "table": {"caslib": caslib, "name": "EIR_BENCH_SCORED_SESSION"}, "to": 100000, "maxRows": 100000})
        scored = _rows(fetched["results"]["Fetch"])
    finally:
        try:
            await client.close_session(session)  # the session-scope tables go with it
        except sas_sync.SasError as error:
            print(f"SAS: could not close CAS session: {error}")
    return compare(scored, "P_REFERENCE_URGENT1")


def main(argv=None) -> int:
    settings = dataclasses.replace(sas_sync.Settings.from_env(), prefix=PREFIX)
    if not settings.configured:
        print("Set SAS_VIYA_URL and SAS_CLIENT_ID + SAS_CLIENT_SECRET (or SAS_ACCESS_TOKEN) in .env")
        return 1

    async def run():
        client = sas_sync.CasClient(settings)
        try:
            rows = await analyse(client, rebuild="--rebuild-features" in (argv or sys.argv[1:]))
            await client.replace_tables({"MODEL_COMPARISON": sas_sync.to_csv("MODEL_COMPARISON", rows, COLUMNS)},
                                        COLUMNS)
            return rows
        finally:
            await client.close()
    try:
        rows = asyncio.run(run())
    except sas_sync.SasError as error:
        print(f"SAS: {error}")
        return 1
    print(f"{settings.caslib}.{PREFIX}FEATURES: KTAS visits, {HOLDOUT_PERCENT}% holdout (_PartInd_ = 1)"
          f"{', drawn again and saved' if '--rebuild-features' in sys.argv[1:] else ', kept if it existed'}")
    for r in rows:
        cutoff = f" at {r['CUTOFF']:.3f}" if r["CUTOFF"] is not None else ""
        print(f"  {r['MODEL']}{cutoff}: caught {r['URGENT_CAUGHT']}/{r['HOLDOUT_URGENT']}, held {r['URGENT_HELD']}, "
              f"missed {r['URGENT_MISSED']}; flagged {r['NOT_URGENT_FLAGGED']}/{r['HOLDOUT_NOT_URGENT']} non-urgent")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    sys.exit(main())
