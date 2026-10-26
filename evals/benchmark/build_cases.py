"""Build the benchmark case files (cases/*.jsonl) from their sources. Offline, deterministic.

    python -m evals.benchmark.build_cases --data evals/local/benchmark_data

Sources (see README.md for licenses and download URLs; checksums are checked here):
- purav: the 10 fictional patient_review_v2.1 cases, with Purav's ESI from his returned review CSV.
- mimic: MIMIC-IV-ED Demo v2.2 (real, de-identified, open access, ODbL). Reference = the ESI the
  triage nurse assigned (triage.acuity). Age from MIMIC-IV Demo v2.2 patients.
- ktas: Moon et al. 2019, PLOS ONE (real, de-identified, CC BY 4.0). Reference = the expert KTAS
  (three KTAS instructors' retrospective review); the triage nurse's KTAS is kept as a human comparison.

Each case is the app's assessment input (backend.case_assessment.read_inputs()["input"]) plus the
reference label and, for the structured datasets, the vital signs the report states (truth for
the extraction check). A report is written from the structured triage fields only: no diagnosis,
disposition or other information from after triage is used.
"""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
CASES_DIR = HERE / "cases"
PURAV_CASES = ROOT / "docs/clinician-annotation/patient_review_v2_1_cases.json"
PURAV_REVIEW = HERE / "data/purav_patient_review_v2.1.csv"
NO_EARLIER_REPORT = "No earlier report provided."  # backend.case_assessment.NO_EARLIER_REPORT

# sha256 of every source file, as downloaded on 2026-10-26 (PhysioNet's own SHA256SUMS agree).
SOURCES = {
    "mimic-iv-ed-demo-2.2/ed/edstays.csv.gz": "fc185cb111a70e9dfd8457c6e1da4f9366dee729e072282995dcd7045a5a3c52",
    "mimic-iv-ed-demo-2.2/ed/triage.csv.gz": "086dadcb49adc5f24bdda0e342ec9cf0fbcb2e9595516c5c101d408434e1e09b",
    "mimic-iv-demo-2.2/hosp/patients.csv.gz": "59dcc6cd8450ddf03ad32cc7d76e41caa4c9633541715987f590c74835cf6fae",
    "ktas/S1_EMR_review_coding_data.xlsx": "f631c951178799179d0fbd74fe979464b955ea1b426222bf015b163b8a24beb0",
}
PURAV_REVIEW_SHA256 = "49077ff182bc79debb76befc74ab8d9ded703ca91b96000f6bdc5a2f961edd08"

# Plausible ranges (backend.case_assessment.VITAL_SIGNS); a value outside is left out of the report.
RANGES = {"hr": (0, 300), "sbp": (0, 300), "dbp": (0, 250), "rr": (0, 100), "spo2": (0, 100), "temp_c": (20, 45)}

WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen").split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(data: Path, name: str) -> Path:
    path = data / name
    if sha256(path) != SOURCES[name]:
        raise ValueError(f"{name}: checksum differs from the one this benchmark was built from")
    return path


def number(value):
    """A clean float from a source cell, or None (blank, '#NULL!', '측불' = could not be measured)."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def spoken(value: float) -> str:
    """A number as a medic says it on the radio: 96 'ninety six', 142 'one forty two', 105 'one oh five'."""
    def below_100(n):
        return WORDS[n] if n < 20 else TENS[n // 10 - 2] + (f" {WORDS[n % 10]}" if n % 10 else "")
    whole = int(value)
    if whole >= 100:
        rest = whole % 100
        text = f"{WORDS[whole // 100]} " + (("hundred" if rest == 0 else f"oh {WORDS[rest]}") if rest < 10
                                            else below_100(rest))
    else:
        text = below_100(whole)
    if not float(value).is_integer():
        text += f" point {WORDS[round(value * 10) % 10]}"
    return text


def report(age, sex, complaint, vitals, temp_unit, mental=None, pain=None, injury=None, unmeasured=(),
           words=False):
    """
    A short EMS radio-style report from triage fields. vitals: name -> value in the source unit
    (temperature in temp_unit). Unreported fields are left out, so the model sees them as unknown.
    """
    say = spoken if words else fmt
    person = {"F": "woman", "M": "man"}.get(sex, "patient")
    age = round(age)  # KTAS gives some ages with a decimal
    article = "an" if str(age).startswith("8") or age in (11, 18) else "a"
    parts = [f"We have {article} {say(age)}-year-old {person}. Chief complaint: {complaint.strip()}."]
    if injury:
        parts.append("This follows an injury.")
    if mental:
        parts.append(mental)
    readings = []
    if "hr" in vitals:
        readings.append(f"pulse {say(vitals['hr'])}")
    if "sbp" in vitals and "dbp" in vitals:
        readings.append(f"BP {say(vitals['sbp'])} over {say(vitals['dbp'])}" if words
                        else f"BP {fmt(vitals['sbp'])}/{fmt(vitals['dbp'])}")
    elif "sbp" in vitals:
        readings.append(f"systolic BP {say(vitals['sbp'])}")
    if "rr" in vitals:
        readings.append(f"respirations {say(vitals['rr'])}")
    if "spo2" in vitals:
        readings.append(f"saturation {say(vitals['spo2'])} percent")
    if "temp" in vitals:
        readings.append(f"temperature {say(vitals['temp'])} {temp_unit}")
    if readings:
        text = ", ".join(readings)
        parts.append(text[0].upper() + text[1:] + ".")
    if unmeasured:
        names = ", ".join(unmeasured[:-1]) + " and " + unmeasured[-1] if len(unmeasured) > 1 else unmeasured[0]
        parts.append(f"{names[0].upper()}{names[1:]} could not be measured.")
    if pain is not None:
        parts.append(pain)
    return " ".join(parts)


def app_input(case_id: str, transcript: str, eta=None) -> dict:
    """The model input in the app's fields (backend.case_assessment.read_inputs)."""
    return {"case_id": case_id, "encounter_id": case_id, "update_number": 1, "elapsed_minutes": 0,
            "eta_minutes": eta, "prior_information": NO_EARLIER_REPORT, "current_transcript": transcript}


def in_range(vitals: dict) -> dict:
    kept = {}
    for name, value in vitals.items():
        key = "temp_c" if name == "temp" else name
        low, high = RANGES[key]
        if value is not None and low <= value <= high:
            kept[name] = value
    return kept


def purav_labels() -> dict:
    """Case id -> Purav's ESI (or 'Cannot assess') from his returned review."""
    if sha256(PURAV_REVIEW) != PURAV_REVIEW_SHA256:
        raise ValueError("Purav's review CSV changed; rebuild with a new protocol version")
    rows = list(csv.reader(PURAV_REVIEW.open(encoding="utf-8-sig")))
    labels, case = {}, None
    for row in rows:
        if row and row[0].startswith("Case ") and "/" in row[0]:
            case = row[0].split("/")[-1].strip()
        elif row and row[0].startswith("ESI level") and case:
            labels[case] = row[1].strip()
    return labels


def build_purav() -> list:
    labels = purav_labels()
    cases = []
    for case in json.loads(PURAV_CASES.read_text(encoding="utf-8")):
        given = {key: case[key] for key in ("case_id", "encounter_id", "update_number", "elapsed_minutes",
                                            "eta_minutes", "prior_information", "current_transcript")}
        label = labels[case["case_id"]]
        cases.append({"dataset": "purav", "case_id": case["case_id"], "subset": ["all"], "input": given,
                      "reference": {"scale": "ESI", "level": int(label) if label.isdigit() else None,
                                    "raw": label, "labeler": "one clinician (Purav), synthetic case"},
                      "truth_vitals": None, "meta": {"age": case["age_years"], "sex": case["sex"]}})
    if len(cases) != 10 or any(c["reference"]["level"] is None for c in cases):
        raise ValueError("expected Purav's ESI on all 10 cases")
    return cases


def build_mimic(data: Path) -> list:
    stays = list(csv.DictReader(gzip.open(checked(data, "mimic-iv-ed-demo-2.2/ed/edstays.csv.gz"), "rt")))
    triage = {r["stay_id"]: r for r in csv.DictReader(gzip.open(checked(data, "mimic-iv-ed-demo-2.2/ed/triage.csv.gz"), "rt"))}
    patients = {r["subject_id"]: r for r in csv.DictReader(gzip.open(checked(data, "mimic-iv-demo-2.2/hosp/patients.csv.gz"), "rt"))}
    cases = []
    for stay in sorted(stays, key=lambda s: s["stay_id"]):
        row = triage[stay["stay_id"]]
        if not row["acuity"]:
            continue  # no ESI recorded: nothing to compare with
        patient = patients[stay["subject_id"]]
        # MIMIC ages: anchor_age in anchor_year, dates shifted consistently per patient; over 89 shown as 91.
        age = int(patient["anchor_age"]) + int(stay["intime"][:4]) - int(patient["anchor_year"])
        temp = number(row["temperature"])
        vitals = in_range({"hr": number(row["heartrate"]), "sbp": number(row["sbp"]), "dbp": number(row["dbp"]),
                           "rr": number(row["resprate"]), "spo2": number(row["o2sat"]),
                           "temp": round((temp - 32) * 5 / 9, 1) if temp and 90 <= temp <= 110 else None})
        if "temp" in vitals:
            vitals["temp"] = temp  # the report states Fahrenheit, as recorded
        pain = number(row["pain"])
        pain_text = (f"Pain {fmt(pain)} out of 10." if pain is not None and 0 <= pain <= 10 and pain.is_integer()
                     else None)  # free-text pain ('unable', 'Critical', 'ett', 13) is left out
        complaint = row["chiefcomplaint"].strip() or "no chief complaint recorded"
        case_id = f"MIMIC-{stay['stay_id']}"
        transcript = report(age, stay["gender"], complaint.lower(), vitals, "Fahrenheit", pain=pain_text)
        ems = stay["arrival_transport"] in ("AMBULANCE", "HELICOPTER")
        cases.append({
            "dataset": "mimic", "case_id": case_id, "subset": ["all"] + (["ems"] if ems else []),
            "input": app_input(case_id, transcript),
            "reference": {"scale": "ESI", "level": int(row["acuity"]), "raw": row["acuity"],
                          "labeler": "triage nurse at the time (MIMIC-IV-ED triage.acuity)"},
            "truth_vitals": truth(vitals, temp_unit="F"),
            "meta": {"age": age, "sex": stay["gender"], "arrival": stay["arrival_transport"],
                     "source_row": {"subject_id": stay["subject_id"], "stay_id": stay["stay_id"]}},
        })
    return cases


def truth(vitals: dict, temp_unit: str) -> dict:
    """What the report states, in the app's units (temperature in °C)."""
    out = {name: value for name, value in vitals.items() if name != "temp"}
    if "temp" in vitals:
        out["temp_c"] = round((vitals["temp"] - 32) * 5 / 9, 1) if temp_unit == "F" else vitals["temp"]
    return out


MENTAL = {"1": "He is alert.", "2": "He responds to voice only.", "3": "He responds only to pain.",
          "4": "He is unresponsive."}
ARRIVAL = {1: "walking", 2: "public EMS (119)", 3: "private car", 4: "private ambulance",
           5: "public transport (police etc.)", 6: "wheelchair", 7: "other"}
UNMEASURED_LABEL = {"SBP": "BP", "HR": "pulse", "RR": "respirations", "BT": "temperature", "Saturation": "saturation"}


def build_ktas(data: Path, words=False) -> list:
    import openpyxl  # evals/requirements.txt

    sheet = openpyxl.load_workbook(checked(data, "ktas/S1_EMR_review_coding_data.xlsx"), read_only=True).active
    rows = list(sheet.iter_rows(values_only=True))
    header = rows[0]
    cases = []
    for index, values in enumerate(rows[1:], start=1):
        row = dict(zip(header, values))
        sex = {1: "F", 2: "M"}[row["Sex"]]
        mental = MENTAL[str(row["Mental"])]
        if sex == "F":
            mental = mental.replace("He ", "She ")
        vitals = in_range({"hr": number(row["HR"]), "sbp": number(row["SBP"]), "dbp": number(row["DBP"]),
                           "rr": number(row["RR"]), "spo2": number(row["Saturation"]), "temp": number(row["BT"])})
        # '측불' (could not be measured) is said as such: an unmeasurable BP is itself a finding.
        unmeasured = list(dict.fromkeys(UNMEASURED_LABEL[k] for k in UNMEASURED_LABEL if str(row[k]).strip() == "측불"))
        nrs = number(row["NRS_pain"])
        if str(row["Pain"]) == "1":
            pain = f"Pain {fmt(nrs)} out of 10." if nrs is not None else "Reports pain; severity not rated."
        else:
            pain = "Reports no pain."
        case_id = f"KTAS-{index:04d}"
        transcript = report(number(row["Age"]), sex, str(row["Chief_complain"]), vitals, "Celsius",
                            mental=mental, pain=pain, injury=row["Injury"] == 2, unmeasured=unmeasured, words=words)
        ems = row["Arrival mode"] in (2, 4)
        cases.append({
            "dataset": "ktas_spoken" if words else "ktas", "case_id": case_id,
            "subset": ["all"] + (["ems"] if ems else []),
            "input": app_input(case_id, transcript),
            "reference": {"scale": "KTAS", "level": int(row["KTAS_expert"]), "raw": row["KTAS_expert"],
                          "labeler": "KTAS experts' retrospective review (Moon et al. 2019)"},
            "truth_vitals": truth(vitals, temp_unit="C"),
            "meta": {"age": number(row["Age"]), "sex": sex, "arrival": ARRIVAL[row["Arrival mode"]],
                     "nurse_ktas": int(row["KTAS_RN"]), "mental": str(row["Mental"]),
                     "source_row": {"sheet_row": index + 1}},
        })
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="Folder with the downloaded sources (README.md)")
    args = parser.parse_args(argv)
    CASES_DIR.mkdir(exist_ok=True)
    builds = {"purav": build_purav(), "mimic": build_mimic(args.data), "ktas": build_ktas(args.data)}
    # The spoken-number variant is only the EMS arrivals: it tests the same cases read as radio speech.
    builds["ktas_spoken"] = [c for c in build_ktas(args.data, words=True) if "ems" in c["subset"]]
    for name, cases in builds.items():
        path = CASES_DIR / f"{name}.jsonl"
        path.write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in cases),
                        encoding="utf-8")
        print(f"{path.relative_to(ROOT)}: {len(cases)} cases, sha256 {sha256(path)}")


if __name__ == "__main__":
    main()
