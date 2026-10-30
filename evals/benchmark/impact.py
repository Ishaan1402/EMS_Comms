"""What Eir could mean for one hospital, a large one and every US ED: the numbers behind the VA
"High impact" page, as two SAS tables.

    python -m evals.benchmark.impact push [--data evals/local/benchmark_data]
    python -m evals.benchmark.impact show [--data ...]      # print the rows, push nothing

    NHAMCS    the CDC's NHAMCS 2022 ED visits (16,025 sampled visits, public-use file), one row per
              visit with its survey weight (PATWT: the US visits it stands for) and PER_AVG_ED
              (PATWT / 5,613 US EDs), ambulance arrival, outcome group and condition. Summing
              either weight in VA gives national or average-ED counts. No free text.
    HOSPITAL  one row per hospital, scenario and step of the chain from those volumes to patients,
              minutes, hours and dollars, each with its evidence class, source and how it is worked out.

Four evidence classes, never mixed in one number without saying so: CDC data (counts from the
weighted NHAMCS file), Eir measured (our benchmark on 1,683 NHAMCS ambulance visits), Published
study (effects of EMS prenotification on other patients, not of Eir) and Assumption (the scenario
settings; a pilot has to measure them). Major trauma is left out: there is no US figure for how
often trauma patients arrive without notice.
"""
import argparse
import asyncio
import dataclasses
import sys
from pathlib import Path

from backend import sas_sync
from .build_cases import checked, nhamcs_outcome

PREFIX = "EIR_IMPACT_"
US_EDS = 5613  # NEDI-USA count of non-federal, non-specialty US EDs (Bennett et al., West J Emerg Med 2024)
LARGE_ED_AMBULANCES = 10000  # the example hospital of the existing Business Impact report
CRITICAL = "ICU, death or surgery from ED"

# Eir on the 1,579 NHAMCS ambulance visits with a known outcome (results/outcomes.json).
EIR_CRITICAL = {"flagged": (110, 155), "held": (38, 155), "placed_3_5": (7, 155)}
EIR_HELD_SHARE = (38 + 95 + 393, 1579)            # all visits Eir held for review ("Cannot assess")
EIR_FALSE_URGENT_SHARE = (224 + 597, 1579)        # put at 1-2 but not critical (ward or sent home)

# Published effects of EMS prenotification (studies of other patients, not of Eir).
STROKE_UNANNOUNCED = 0.33   # Lin 2012, GWTG-Stroke: 67.0% of 371,988 EMS stroke patients prenotified
STEMI_UNANNOUNCED = 0.59    # Shavadia 2018, ACTION Registry: cath lab preactivated 10+ min ahead 41%
STEMI_DEATHS_AVOIDED = 0.015  # 5.6 deaths per 100 (Rymer) with OR 0.72 (prehospital ECG + notice) -> 4.1

SCENARIOS = {  # share of the notice gap Eir closes, bed request minutes earlier, handoff minutes saved
    "Cautious": (0.25, 15, 1),
    "Middle": (0.50, 30, 2),
    "Strong": (0.80, 45, 3),
}
# Carried over from the Business Impact report's defaults so both pages agree.
REACHED = 0.80          # arrivals Eir assesses in time
ACTIONABLE = 0.50       # admissions where an earlier bed request can help
USABLE = 0.60           # freed ED hours usable for new patients
HOURS_PER_PATIENT = 3   # ED hours an extra patient uses
DOLLARS_PER_PATIENT = 200
REVIEW_MINUTES = 3      # clinician check of a held case
PREP_MINUTES = 2        # preparing for an urgent flag

CDC, EIR, STUDY, ASSUMED, WORKED = ("CDC data (NHAMCS 2022)", "Eir measured", "Published study",
                                    "Assumption (needs a pilot)", "Worked out from the rows above")

COLUMNS = {
    "NHAMCS": [
        ("VISIT_NO", "num", "Sampled visit"), ("AMBULANCE", "num", "Arrived by ambulance (1/0)"),
        ("OUTCOME_GROUP", "char", "What happened (from the ED disposition)"),
        ("CONDITION", "char", "Time-critical condition (ED diagnosis)"), ("AGE", "num", "Age"),
        ("PATWT", "num", "US visits this sampled visit stands for (survey weight)"),
        ("PER_AVG_ED", "num", "Visits a year at an average US ED (PATWT / 5,613)"),
    ],
    "HOSPITAL": [
        ("HOSPITAL", "char", "Hospital"), ("SCENARIO", "char", "Scenario"), ("STEP", "num", "Step"),
        ("MEASURE", "char", "Measure, per year"), ("VALUE", "num", "Value"), ("UNIT", "char", "Unit"),
        ("EVIDENCE", "char", "Evidence class"), ("SOURCE", "char", "Source"), ("HOW", "char", "How it is worked out"),
    ],
}


def condition(row) -> str:
    codes = [str(row[f"DIAG{i}"]) for i in range(1, 6)]
    if any(c[:4] in ("I210", "I211", "I212", "I213") for c in codes):
        return "Heart attack (STEMI)"
    if any(c[:3] == "I21" for c in codes):
        return "Heart attack (other)"
    if any(c[:3] == "I63" for c in codes):
        return "Ischemic stroke"
    return "Other"


def visit_rows(data: Path) -> list:
    import pandas  # the CDC ships the file as a SAS dataset

    frame = pandas.read_sas(checked(data, "nhamcs/ed2022_sas.sas7bdat"), encoding="latin-1")
    return [{"VISIT_NO": i + 1, "AMBULANCE": int(row["ARREMS"] == 1), "OUTCOME_GROUP": nhamcs_outcome(row),
             "CONDITION": condition(row), "AGE": int(row["AGE"]), "PATWT": round(float(row["PATWT"]), 4),
             "PER_AVG_ED": round(float(row["PATWT"]) / US_EDS, 6)}
            for i, (_, row) in enumerate(frame.iterrows())]


def volumes(visits: list) -> dict:
    """National counts a year (sum of PATWT), and the sampled visits behind each."""
    def count(keep):
        chosen = [v for v in visits if keep(v)]
        return sum(v["PATWT"] for v in chosen), len(chosen)
    amb = lambda v: v["AMBULANCE"] == 1  # noqa: E731
    return {
        "visits": count(lambda v: True),
        "ambulance": count(amb),
        "critical": count(lambda v: amb(v) and v["OUTCOME_GROUP"] == CRITICAL),
        "admitted": count(lambda v: amb(v) and v["OUTCOME_GROUP"] in (CRITICAL, "Ward admission")),
        "stroke": count(lambda v: amb(v) and v["CONDITION"] == "Ischemic stroke"),
        "stemi": count(lambda v: amb(v) and v["CONDITION"] == "Heart attack (STEMI)"),
    }


def share(pair) -> float:
    return pair[0] / pair[1]


def hospital_rows(national: dict) -> list:
    """The chain for each hospital and scenario. national: {name: (US visits a year, sampled visits)}."""
    hospitals = {"Average US ED": 1 / US_EDS,
                 "Large ED (10,000 ambulances a year)": LARGE_ED_AMBULANCES / national["ambulance"][0],
                 f"All {US_EDS:,} US EDs": 1.0}
    rows = []
    for hospital, scale in hospitals.items():
        n = {name: total * scale for name, (total, _) in national.items()}
        for scenario, (gap_closed, bed_minutes, handoff_minutes) in SCENARIOS.items():
            start = len(rows)

            def add(measure, value, unit, evidence, source, how):
                rows.append({"HOSPITAL": hospital, "SCENARIO": scenario, "STEP": len(rows) - start + 1,
                             "MEASURE": measure, "VALUE": round(value, 2), "UNIT": unit, "EVIDENCE": evidence,
                             "SOURCE": source, "HOW": how})
            nhamcs = "CDC NHAMCS 2022 public-use file, weighted"
            per = f"US total / {US_EDS:,} EDs" if hospital == "Average US ED" else (
                "scaled to 10,000 ambulance arrivals" if hospital.startswith("Large") else "US total")
            add("ED visits", n["visits"], "visits", CDC, nhamcs, per)
            add("Ambulance arrivals", n["ambulance"], "patients", CDC, nhamcs, per)
            add("Critical ambulance arrivals (ICU, cath lab, surgery or death)", n["critical"], "patients", CDC,
                nhamcs, per)
            for key, label in (("flagged", "Critical arrivals Eir flags urgent (ESI 1-2) before they arrive"),
                               ("held", "Critical arrivals Eir holds for a clinician check"),
                               ("placed_3_5", "Critical arrivals Eir places at ESI 3-5")):
                k, of = EIR_CRITICAL[key]
                add(label, n["critical"] * k / of, "patients", EIR,
                    f"Eir on 1,683 NHAMCS ambulance visits: {k} of {of} critical", "critical arrivals x share")
            add("Stroke arrivals by ambulance", n["stroke"], "patients", CDC,
                f"{nhamcs} ({national['stroke'][1]} sampled visits)", per)
            add("Stroke arrivals with no advance notice", n["stroke"] * STROKE_UNANNOUNCED, "patients", STUDY,
                "Lin et al. 2012, GWTG-Stroke: 67% prenotified", "stroke arrivals x 33%")
            add("Stroke patients newly announced through Eir", n["stroke"] * STROKE_UNANNOUNCED * gap_closed,
                "patients", ASSUMED, f"Scenario: Eir closes {gap_closed:.0%} of the notice gap",
                "unannounced x share closed; prenotified patients were scanned in 26 vs 31 min (Lin 2012)")
            add("Heart attack (STEMI) arrivals by ambulance", n["stemi"], "patients", CDC,
                f"{nhamcs} ({national['stemi'][1]} sampled visits: indicative only)", per)
            add("STEMI arrivals with no cath lab alert 10+ min ahead", n["stemi"] * STEMI_UNANNOUNCED, "patients",
                STUDY, "Shavadia et al. 2018, ACTION Registry: 41% preactivated", "STEMI arrivals x 59%")
            stemi_new = n["stemi"] * STEMI_UNANNOUNCED * gap_closed
            add("STEMI patients newly announced through Eir", stemi_new, "patients", ASSUMED,
                f"Scenario: Eir closes {gap_closed:.0%} of the notice gap", "unannounced x share closed")
            add("STEMI deaths avoided", stemi_new * STEMI_DEATHS_AVOIDED, "lives", STUDY,
                "OR 0.72 with prehospital ECG + notice (Circ Rep 2022 meta-analysis) on 5.6 deaths per 100 (Rymer)",
                "newly announced x 1.5 per 100")
            add("Admitted ambulance arrivals", n["admitted"], "patients", CDC, nhamcs, per)
            eligible = n["admitted"] * REACHED * ACTIONABLE
            add("Admissions where an earlier bed request can help", eligible, "patients", ASSUMED,
                "Business Impact report defaults: 80% reached in time, 50% actionable", "admitted x 80% x 50%")
            add("Bed request sent earlier", bed_minutes, "minutes per admission", ASSUMED,
                f"Scenario: {bed_minutes} min", "set by the scenario")
            hours = eligible * bed_minutes / 60
            add("ED treatment hours freed", hours, "hours", WORKED, "", "eligible admissions x minutes / 60")
            extra = hours * USABLE / HOURS_PER_PATIENT
            add("Extra patients the ED can see", extra, "patients", ASSUMED,
                "60% of freed hours usable, 3 ED hours per patient (report defaults)", "hours x 60% / 3")
            dollars = extra * DOLLARS_PER_PATIENT
            add("Value of the extra patients", dollars, "$", ASSUMED, "$200 contribution per patient (report default)",
                "extra patients x $200")
            reached = n["ambulance"] * REACHED
            saved = reached * handoff_minutes / 60
            add("Staff hours saved on radio handoffs", saved, "hours", ASSUMED,
                f"Scenario: {handoff_minutes} min per arrival reached", "arrivals x 80% x minutes / 60")
            review = reached * share(EIR_HELD_SHARE) * REVIEW_MINUTES / 60
            add("Staff hours checking cases Eir held", -review, "hours", EIR,
                f"Eir held {EIR_HELD_SHARE[0]} of {EIR_HELD_SHARE[1]} NHAMCS visits; 3 min each (assumed)",
                "arrivals x 80% x 33% x 3 min")
            prep = reached * share(EIR_FALSE_URGENT_SHARE) * PREP_MINUTES / 60
            add("Staff hours preparing for urgent flags that were not critical", -prep, "hours", EIR,
                f"Eir put {EIR_FALSE_URGENT_SHARE[0]} of {EIR_FALSE_URGENT_SHARE[1]} NHAMCS visits at 1-2 that "
                "were not critical; 2 min each (assumed)", "arrivals x 80% x 52% x 2 min")
            add("Net staff hours", saved - review - prep, "hours", WORKED, "", "saved - checking - preparing")
            add("Value per ambulance arrival", dollars / n["ambulance"], "$", WORKED, "",
                "value of the extra patients / ambulance arrivals")
            add("Share of the notice gap Eir closes", gap_closed * 100, "%", ASSUMED,
                f"Scenario: {gap_closed:.0%}", "set by the scenario")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("push", "show"))
    parser.add_argument("--data", type=Path, default=Path(__file__).parents[1] / "local" / "benchmark_data")
    args = parser.parse_args(argv)
    visits = visit_rows(args.data)
    national = volumes(visits)
    rows = hospital_rows(national)
    for name, (total, sampled) in national.items():
        print(f"US {name}: {total:,.0f} a year ({sampled} sampled visits), average ED {total / US_EDS:,.1f}")
    for r in rows:
        if r["SCENARIO"] == "Middle":
            print(f"  {r['HOSPITAL'][:22]:22s} {r['STEP']:2d} {r['MEASURE'][:62]:62s} {r['VALUE']:>14,.1f} {r['UNIT']}")
    if args.command == "show":
        return 0
    settings = dataclasses.replace(sas_sync.Settings.from_env(), prefix=PREFIX)
    if not settings.configured:
        print("Set SAS_VIYA_URL and SAS_CLIENT_ID + SAS_CLIENT_SECRET (or SAS_ACCESS_TOKEN) in .env")
        return 1
    tables = {"NHAMCS": visits, "HOSPITAL": rows}

    async def run():
        client = sas_sync.CasClient(settings)
        try:
            await client.replace_tables({name: sas_sync.to_csv(name, data, COLUMNS) for name, data in tables.items()},
                                        COLUMNS)
        finally:
            await client.close()
    try:
        asyncio.run(run())
    except sas_sync.SasError as error:
        print(f"SAS: {error}")
        return 1
    for name, data in tables.items():
        print(f"{settings.caslib}.{PREFIX}{name}: {len(data)} rows")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    sys.exit(main())
