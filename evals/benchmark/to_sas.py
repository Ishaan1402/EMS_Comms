"""Load the benchmark results into SAS Viya (CAS) as EIR_BENCH_* tables, through the app's own SAS client.

    python -m evals.benchmark.to_sas push        # replace and save every EIR_BENCH_* table in the caslib
    python -m evals.benchmark.to_sas csv DIR     # the same tables as CSV + schema.json (for sas/load_csvs.py)

Reads results/raw/ and results/metrics.json (run `python -m evals.benchmark.score` first). Uses the
SAS settings in .env, as backend.sas_sync does (the caslib is SAS_CASLIB, default Public), but always
the prefix EIR_BENCH_, so the app's own tables are never touched. Each table is replaced the way the
app's sync replaces its own: staging table, swap, saved as .sashdat so it survives a CAS restart.
No free text leaves the repo: the reports and the AI's reasons stay in results/. MIMIC stays go as
MIMIC-001... (not their stay IDs) and with their age band only; exact ages go only for the KTAS
visits, whose source publishes them per row.

    CALLS    one row per AI assessment call (the last attempt per dataset, case and repeat): the
             reference level, the AI's level, how they compare, the patient's age band, sex,
             arrival and stated vitals, tokens, latency and cost
    HEADLINE where the AI, the triage nurses and a simple vitals rule put the urgent visits
             (reference 1-2), one row per set, rater and outcome: the safety chart
    METRICS  every number in metrics.json, one per row, with numerator, denominator and 95% interval
"""
import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

from backend import sas_sync
from .run_live import CASES_DIR, load_cases
from .score import ai_level, analyse_assessments, read_jsonl

HERE = Path(__file__).parent
RESULTS = HERE / "results"
PREFIX = "EIR_BENCH_"

SEX = {"M": "Male", "F": "Female", "male": "Male", "female": "Female"}
KTAS_MENTAL = {"1": "Alert", "2": "Responds to voice", "3": "Responds to pain", "4": "Unresponsive"}
NO_LEVEL = "No level (Needs review)"

COLUMNS = {
    "CALLS": [
        ("DATASET", "char", "Benchmark set (ktas / ktas_spoken / mimic / purav)"),
        ("CASE_ID", "char", "Case (MIMIC: a sequence number, not the stay ID)"),
        ("REPEAT", "num", "Repeat of the same input (1 = primary result)"),
        ("PRIMARY", "num", "Primary result: repeat 1 (1/0)"),
        ("EMS_ARRIVAL", "num", "Arrived by ambulance or helicopter (1/0)"),
        ("ARRIVAL", "char", "Arrival mode as recorded"),
        ("REFERENCE_SCALE", "char", "Reference scale (KTAS or ESI)"),
        ("REFERENCE_LEVEL", "num", "Reference level (1 = most urgent)"),
        ("REFERENCE_URGENT", "num", "Reference level 1-2 (1/0)"),
        ("AI_STATUS", "char", "App status of the AI's answer"),
        ("AI_GAVE_LEVEL", "num", "AI gave a level (1/0); 0 = Cannot assess, shown as Needs review"),
        ("AI_ESI", "num", "AI's suggested ESI level"),
        ("AI_URGENT", "num", "AI gave level 1-2 (1/0; missing when it gave no level)"),
        ("LEVEL_DIFFERENCE", "num", "AI level minus reference (above 0 = AI less urgent)"),
        ("AGREEMENT", "char", "Exact / AI more urgent / AI less urgent / No level"),
        ("URGENT_OUTCOME", "char", "For reference 1-2: Given 1-2 / Given 3-5 / No level"),
        ("RISK_SCORE", "num", "AI's risk score (1-10)"),
        ("NURSE_KTAS", "num", "Triage nurse's KTAS (KTAS set only)"),
        ("NURSE_LESS_URGENT", "num", "Triage nurse's KTAS less urgent than the experts' (1/0)"),
        ("SIMPLE_RULE", "num", "Simple vitals-and-consciousness rule's level (ours, not clinical)"),
        ("NUMBER_FLAG", "num", "Handoff number check flagged the answer (1/0)"),
        ("ATTEMPTS", "num", "Attempts for this call"),
        ("AGE", "num", "Age (years; KTAS only)"),
        ("AGE_BAND", "char", "Age band"),
        ("SEX", "char", "Sex"),
        ("MENTAL_STATE", "char", "Mental state at triage (KTAS set only)"),
        ("HR", "num", "Heart rate stated in the report"),
        ("SBP", "num", "Systolic BP stated in the report"),
        ("DBP", "num", "Diastolic BP stated in the report"),
        ("RR", "num", "Respiratory rate stated in the report"),
        ("SPO2", "num", "SpO2 stated in the report"),
        ("TEMP_C", "num", "Temperature (C) stated in the report"),
        ("LATENCY_MS", "num", "Model latency (ms)"),
        ("INPUT_TOKENS", "num", "Input tokens"),
        ("OUTPUT_TOKENS", "num", "Output tokens"),
        ("COST_USD", "num", "Estimated cost (USD, list price)"),
        ("SCORER_VERSION", "char", "Model / prompt"),
    ] + sas_sync._time_columns("CALLED", "Call completed"),
    "HEADLINE": [
        ("SET_NAME", "char", "Urgent visits (reference 1-2) in"),
        ("RATER", "char", "Who placed them"),
        ("OUTCOME", "char", "Placed at"),
        ("VISITS", "num", "Visits"),
        ("SET_VISITS", "num", "Urgent visits in the set"),
        ("SHARE", "num", "Share of the set's urgent visits"),
    ],
    "METRICS": [
        ("SECTION", "char", "Section of metrics.json"),
        ("ANALYSIS", "char", "Analysis (dataset/subset, check or step)"),
        ("METRIC", "char", "Metric (path in metrics.json)"),
        ("NUMERATOR", "num", "Numerator"),
        ("DENOMINATOR", "num", "Denominator"),
        ("VALUE", "num", "Value"),
        ("ASE", "num", "Asymptotic standard error (kappas)"),
        ("CI_LOW", "num", "95% interval, low"),
        ("CI_HIGH", "num", "95% interval, high"),
    ],
}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _agreement(reference, level):
    if level is None:
        return NO_LEVEL
    if level == reference:
        return "Exact"
    return "AI more urgent" if level < reference else "AI less urgent"


def calls_rows(cases: dict, records: list, run_infos: list) -> list:
    """One row per recorded assessment call, from the same pairing score.py uses."""
    _, pairs, by_key = analyse_assessments(cases, records)
    price = next((r["price_per_million_tokens"] for r in run_infos if r.get("price_per_million_tokens")), None)
    scorer = next((f"{r['scoring_model']} / {r['assessment_prompt']}" for r in run_infos if r.get("scoring_model")), None)
    # MIMIC stays are numbered in case-file order instead of carrying their stay IDs into SAS.
    mimic_ids = {c["case_id"]: f"MIMIC-{i:03d}" for i, c in enumerate(cases["mimic"], start=1)}
    rows = []
    for pair in pairs:
        dataset, case_id, repeat = pair["dataset"], pair["case_id"], pair["repeat"]
        case, record = cases[dataset + "_by_id"][case_id], by_key[(dataset, case_id, repeat)]
        meta, vitals = case["meta"], case["truth_vitals"] or {}
        reference, level = case["reference"]["level"], ai_level(record)
        usage = record.get("usage") or {}
        cost = None
        if price and usage.get("input_tokens") is not None and usage.get("output_tokens") is not None:
            cost = round((usage["input_tokens"] * price[0] + usage["output_tokens"] * price[1]) / 1e6, 8)
        age = _number(meta.get("age"))
        exact_age = age if dataset.startswith("ktas") else None
        nurse = _number(meta.get("nurse_ktas"))
        row = {
            "DATASET": dataset, "CASE_ID": mimic_ids.get(case_id, case_id), "REPEAT": repeat,
            "PRIMARY": int(repeat == 1),
            "EMS_ARRIVAL": pair["ems_arrival"], "ARRIVAL": meta.get("arrival"),
            "REFERENCE_SCALE": case["reference"]["scale"], "REFERENCE_LEVEL": reference,
            "REFERENCE_URGENT": int(reference <= 2),
            "AI_STATUS": pair["ai_status"], "AI_GAVE_LEVEL": int(level is not None), "AI_ESI": level,
            "AI_URGENT": None if level is None else int(level <= 2),
            "LEVEL_DIFFERENCE": None if level is None else level - reference,
            "AGREEMENT": _agreement(reference, level),
            "URGENT_OUTCOME": None if reference > 2 else NO_LEVEL if level is None
            else "Given 1-2" if level <= 2 else "Given 3-5",
            "RISK_SCORE": _number(pair["risk_score"]),
            "NURSE_KTAS": nurse, "NURSE_LESS_URGENT": None if nurse is None else int(nurse > reference),
            "SIMPLE_RULE": _number(pair["simple_rule"]), "NUMBER_FLAG": pair["number_flag"],
            "ATTEMPTS": pair["attempts"],
            "AGE": exact_age, "AGE_BAND": None if age is None else "65 and over" if age >= 65 else "Under 65",
            "SEX": SEX.get(meta.get("sex")), "MENTAL_STATE": KTAS_MENTAL.get(meta.get("mental")),
            "HR": vitals.get("hr"), "SBP": vitals.get("sbp"), "DBP": vitals.get("dbp"), "RR": vitals.get("rr"),
            "SPO2": vitals.get("spo2"), "TEMP_C": vitals.get("temp_c"),
            "LATENCY_MS": usage.get("latency_ms"), "INPUT_TOKENS": usage.get("input_tokens"),
            "OUTPUT_TOKENS": usage.get("output_tokens"), "COST_USD": cost, "SCORER_VERSION": scorer,
        }
        rows.append(sas_sync._with_times(row, called=record.get("completed_at")))
    return rows


# (set name, rows of CALLS in it); urgent = reference 1-2, primary result only.
HEADLINE_SETS = (
    ("KTAS ambulance arrivals", lambda r: r["DATASET"] == "ktas" and r["EMS_ARRIVAL"]),
    ("KTAS all visits", lambda r: r["DATASET"] == "ktas"),
    ("MIMIC ambulance arrivals", lambda r: r["DATASET"] == "mimic" and r["EMS_ARRIVAL"]),
)


def headline_rows(calls: list) -> list:
    """The AI next to the nurses (KTAS only) and the simple rule on the same urgent visits."""
    raters = (
        ("Eir AI", lambda r: r["URGENT_OUTCOME"]),
        ("Triage nurses", lambda r: None if r["NURSE_KTAS"] is None
         else "Given 1-2" if r["NURSE_KTAS"] <= 2 else "Given 3-5"),
        ("Simple vitals rule", lambda r: None if r["SIMPLE_RULE"] is None
         else "Given 1-2" if r["SIMPLE_RULE"] <= 2 else "Given 3-5"),
    )
    rows = []
    for set_name, chosen in HEADLINE_SETS:
        urgent = [r for r in calls if r["PRIMARY"] and r["REFERENCE_URGENT"] and chosen(r)]
        for rater, outcome_of in raters:
            outcomes = [outcome_of(r) for r in urgent]
            if any(o is None for o in outcomes):
                continue  # this rater has no level for these visits (nurses outside KTAS)
            for outcome in ("Given 1-2", NO_LEVEL, "Given 3-5"):
                if rater != "Eir AI" and outcome == NO_LEVEL:
                    continue
                visits = outcomes.count(outcome)
                rows.append({"SET_NAME": set_name, "RATER": rater, "OUTCOME": outcome, "VISITS": visits,
                             "SET_VISITS": len(urgent), "SHARE": round(visits / len(urgent), 4)})
    return rows


def metrics_rows(metrics: dict) -> list:
    """Every number in metrics.json as (section, analysis, metric path) with its interval if it has one."""
    rows = []

    def add(section, analysis, path, node):
        if isinstance(node, dict) and "value" in node and not isinstance(node["value"], dict):
            rows.append({"SECTION": section, "ANALYSIS": analysis, "METRIC": "/".join(path),
                         "NUMERATOR": node.get("k"), "DENOMINATOR": node.get("n"), "VALUE": _number(node["value"]),
                         "ASE": node.get("ase"), "CI_LOW": node.get("low"), "CI_HIGH": node.get("high")})
        elif isinstance(node, dict):
            for key, child in node.items():
                add(section, analysis, path + [str(key)], child)
        elif _number(node) is not None:
            rows.append({"SECTION": section, "ANALYSIS": analysis, "METRIC": "/".join(path), "VALUE": node})

    for section in ("esi", "consistency", "guardrail_mutation_tests", "extraction", "operations"):
        for analysis, node in metrics[section].items():
            add(section, analysis, [], node)
    for section in ("model_unsupported_numbers_all_runs", "model_unsupported_numbers_on_real_outputs"):
        add(section, "all", ["flagged"], metrics[section])
    return rows


def build_tables(results: Path = RESULTS) -> dict:
    cases = {}
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        cases[path.stem] = load_cases(path.stem)
        cases[path.stem + "_by_id"] = {c["case_id"]: c for c in cases[path.stem]}
    raw = results / "raw"
    calls = calls_rows(cases, read_jsonl(raw / "assessments.jsonl"), read_jsonl(raw / "runs.jsonl"))
    return {
        "CALLS": calls,
        "HEADLINE": headline_rows(calls),
        "METRICS": metrics_rows(json.loads((results / "metrics.json").read_text(encoding="utf-8"))),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("push", "csv"))
    parser.add_argument("dir", nargs="?", type=Path, help="output folder for csv")
    parser.add_argument("--results", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    tables = build_tables(args.results)
    if args.command == "csv":
        if not args.dir:
            parser.error("csv needs an output folder")
        args.dir.mkdir(parents=True, exist_ok=True)
        for name, rows in tables.items():
            (args.dir / f"{PREFIX}{name}.csv").write_bytes(sas_sync.to_csv(name, rows, COLUMNS))
            print(f"{args.dir / (PREFIX + name)}.csv: {len(rows)} rows")
        (args.dir / "schema.json").write_text(json.dumps(sas_sync.schema(tables, PREFIX, COLUMNS), indent=2) + "\n")
        return 0

    settings = dataclasses.replace(sas_sync.Settings.from_env(), prefix=PREFIX)
    if not settings.configured:
        print("Set SAS_VIYA_URL and SAS_CLIENT_ID + SAS_CLIENT_SECRET (or SAS_ACCESS_TOKEN) in .env")
        return 1

    async def push():
        client = sas_sync.CasClient(settings)
        try:
            await client.replace_tables({name: sas_sync.to_csv(name, rows, COLUMNS) for name, rows in tables.items()},
                                        COLUMNS)
        finally:
            await client.close()
    try:
        asyncio.run(push())
    except sas_sync.SasError as error:
        print(f"SAS: {error}")
        return 1
    for name, rows in tables.items():
        print(f"{settings.caslib}.{PREFIX}{name}: {len(rows)} rows{' (saved)' if settings.save_tables else ''}")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    sys.exit(main())
