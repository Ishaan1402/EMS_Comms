"""Build a replay scenario from the evaluation's pilot cases (KAN-13), so the running app can be
scored against the clinician's answers.

    python3 demo/pilot_scenario.py --pilot docs/clinician-annotation/pilot_cases.json \\
        --queues evals/queues.json --out demo/pilot_cases_scenario.json [--cases SYN001,SYN002]
    python3 demo/replay.py --scenario demo/pilot_cases_scenario.json --typed --seconds-per-minute 60
    python -m evals score --references ... --predictions demo/runs/<run_id>-predictions.json --out ...

Each case is replayed the way the dataset describes it. A case with an earlier report sends that
report (prior_information without its "Eight minutes earlier:" phrase), the doctor acknowledges
it, and the current report arrives elapsed_minutes later with the case's ETA; a case without one
sends a single report. The replay then writes the app's final assessment of every case as model
predictions in the harness's format (demo/runs/<run_id>-predictions.json).

Cases run three at a time, one per demo EMT (an EMT has one open case at a time), with each queue
exercise group together so its queue order is recorded while all three patients are inbound.
At --seconds-per-minute 60 the model sees the dataset's elapsed_minutes (about 30 minutes for all
ten cases); faster runs compress it. --typed sends the dataset's text; without it the reports are
spoken (python3 demo/make_audio.py --scenario ...) and transcribed, which tests transcription too.

The pilot cases are for development only: don't tune prompts on these results and then report
held-out accuracy (evals/README.md).
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Direct script execution adds demo/ rather than the repository to sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.contracts import case_dataset_version, load_cases as load_eval_cases

EMTS = ["emt.wilson", "emt.garcia", "emt.lee"]
NO_EARLIER_REPORT = "No earlier report provided."
CASE_FIELDS = ("case_id", "encounter_id", "update_number", "elapsed_minutes", "eta_minutes",
               "prior_information", "current_transcript")


def load_cases(path: Path) -> dict:
    """case_id -> case, from pilot_cases.json (rows of the seven harness fields, or objects)."""
    cases = load_eval_cases(path)
    return {case["case_id"]: case for case in cases}


def earlier_report(prior: str) -> str:
    """'Eight minutes earlier: heart rate 104, ...' -> 'Heart rate 104, ...' (the time is when it's sent)."""
    # Remove only the elapsed-time lead-in, never arbitrary clinical text containing a colon.
    text = re.sub(r"^[A-Za-z -]+ minutes? earlier:\s*", "", prior, count=1)
    return text[0].upper() + text[1:]


def groups(case_ids: list, queues: list) -> list:
    """[(queue_id or None, case ids)]: each queue exercise whose cases are all selected, then the rest in threes."""
    remaining, result = list(case_ids), []
    for queue in queues:
        if len(queue["case_ids"]) <= len(EMTS) and set(queue["case_ids"]) <= set(remaining):
            result.append((queue["queue_id"], list(queue["case_ids"])))
            remaining = [c for c in remaining if c not in queue["case_ids"]]
    result += [(None, remaining[i:i + len(EMTS)]) for i in range(0, len(remaining), len(EMTS))]
    return result


def build(cases: dict, case_ids: list, queues: list, hospital_code: str = "GEN", doctor: str = "dr.smith") -> dict:
    version = case_dataset_version(list(cases.values())) or "pilot_v1"
    patients, events, start = [], [], 0
    for queue_id, group in groups(case_ids, queues):
        last = start
        for emt, case_id in zip(EMTS, group):
            case = cases[case_id]
            has_earlier = case["prior_information"].strip() != NO_EARLIER_REPORT
            reports = {"update": case["current_transcript"]}
            events.append({"minute": start, "action": "start_case", "patient": case_id})
            if has_earlier:
                reports["initial"] = earlier_report(case["prior_information"])
                events += [{"minute": start, "action": "audio_report", "patient": case_id, "report": "initial"},
                           {"minute": start + 0.5, "action": "acknowledge", "patient": case_id}]
            current_at = start + (case["elapsed_minutes"] if has_earlier else 0)
            events.append({"minute": current_at, "action": "audio_report", "patient": case_id, "report": "update"})
            if has_earlier:
                events.append({"minute": current_at, "action": "eta", "patient": case_id, "minutes": case["eta_minutes"]})
            patients.append({
                "source_case_id": case_id,
                "emt": emt,
                "ems_unit": case_id,
                "patient_info": None,  # nothing the dataset doesn't say
                "initial_eta_minutes": case["eta_minutes"] + (case["elapsed_minutes"] if has_earlier else 0),
                "reports": reports,
            })
            last = max(last, current_at)
        end = last + 1
        if queue_id:
            events.append({"minute": end, "action": "snapshot_queue", "queue_id": queue_id, "patients": group})
        events += [{"minute": end + 0.5, "action": "finish_case", "patient": case_id} for case_id in group]
        start = end + 1
    return {
        "scenario_id": "pilot-cases-v1" if version == "pilot_v1" else version.replace("_", "-"),
        "dataset_version": version,
        "provenance": ("Generated by demo/pilot_scenario.py from the evaluation's pilot cases (fictional, "
                       "AI-authored EMS reports; not real patients). Current reports are verbatim; earlier "
                       "reports are prior_information without the leading time phrase. Development cases only."),
        "hospital_code": hospital_code,
        "doctor": doctor,
        "patients": patients,
        "events": sorted(events, key=lambda e: e["minute"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pilot", type=Path, default=Path("docs/clinician-annotation/pilot_cases.json"))
    parser.add_argument("--queues", type=Path,
                        help="Queue exercise groups; the original pilot defaults to evals/queues.json")
    parser.add_argument("--cases", help="comma-separated case IDs (default: all)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cases = load_cases(args.pilot)
    case_ids = args.cases.split(",") if args.cases else list(cases)
    unknown = [c for c in case_ids if c not in cases]
    if unknown:
        parser.error(f"unknown case IDs: {', '.join(unknown)}")
    queues_path = args.queues or (None if case_dataset_version(list(cases.values()))
                                 else Path("evals/queues.json"))
    queues = json.loads(queues_path.read_text()) if queues_path and queues_path.exists() else []
    scenario = build(cases, case_ids, queues)
    args.out.write_text(json.dumps(scenario, indent=1, ensure_ascii=False) + "\n")
    last = scenario["events"][-1]["minute"]
    print(f"{len(case_ids)} cases, {last:g} scenario minutes -> {args.out}")


if __name__ == "__main__":
    main()
