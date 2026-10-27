"""Score a recorded run. Offline and deterministic: the same raw files always give the same numbers.

    python -m evals.benchmark.score --raw evals/benchmark/results/raw --out evals/benchmark/results

Writes metrics.json (every number, with its numerator and denominator), SUMMARY.md, and CSVs
for audit and for SAS (esi_pairs.csv; sas/eir_ai_agreement.sas recomputes the kappas).
"""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import guardrails
from .run_live import CASES_DIR, extraction_info, load_cases
from .stats import agreement, kappa, percentile, wilson

NUMBER_FLAG = "not found in the crew's reports"
# (dataset, subset) analysed; repeat 1 of each case is the primary result (PROTOCOL.md).
ANALYSES = [("purav", "all"), ("mimic", "all"), ("mimic", "ems"), ("ktas", "all"), ("ktas", "ems"),
            ("ktas_spoken", "all"), ("nhamcs", "all")]
LABELS = {
    ("purav", "all"): "Purav's 10 synthetic cases (one clinician's ESI)",
    ("mimic", "all"): "MIMIC-IV-ED Demo, all stays with an ESI (triage nurse's ESI)",
    ("mimic", "ems"): "MIMIC-IV-ED Demo, ambulance/helicopter arrivals (triage nurse's ESI)",
    ("ktas", "all"): "KTAS dataset, all patients (expert KTAS)",
    ("ktas", "ems"): "KTAS dataset, ambulance arrivals (expert KTAS)",
    ("ktas_spoken", "all"): "KTAS ambulance arrivals, numbers spoken as words (expert KTAS)",
    ("nhamcs", "all"): "NHAMCS 2022, adult ambulance arrivals from the scene (ED triage level)",
}


def read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def latest(records) -> dict:
    """The last attempt for each (dataset, case, repeat); earlier failed attempts stay counted."""
    out = {}
    for record in records:
        out[(record["dataset"], record["case_id"], record["repeat"])] = record
    return out


def ai_level(record):
    if record is None or record["status"] != "ok":
        return None
    level = record["app_fields"].get("esi_level")
    return level if isinstance(level, int) else None


def rule_baseline(case) -> int:
    """
    A deliberately simple vitals-and-consciousness rule (ours, not a clinical tool), to show what
    the AI adds over reading the numbers alone. Never gives 4 or 5.
    """
    v, mental = case["truth_vitals"] or {}, case["meta"].get("mental")
    if mental in ("3", "4") or v.get("sbp", 999) < 70 or v.get("spo2", 100) < 80:
        return 1
    if (mental == "2" or v.get("hr", 0) > 100 or v.get("rr", 0) > 20 or v.get("spo2", 100) < 92
            or v.get("sbp", 999) < 90):
        return 2
    return 3


def round_floats(value, places=4):
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, dict):
        return {k: round_floats(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [round_floats(v, places) for v in value]
    return value


def analyse_assessments(cases, records):
    by_key = latest(records)
    attempts = Counter((r["dataset"], r["case_id"], r["repeat"]) for r in records)
    results, pairs_rows = {}, []
    for dataset, subset in ANALYSES:
        chosen = [c for c in cases[dataset] if subset in c["subset"]]
        primary = [(c, by_key.get((dataset, c["case_id"], 1))) for c in chosen]
        outcome = Counter()
        pairs, rule_pairs, nurse_pairs = [], [], []
        for case, record in primary:
            if record is None:
                outcome["not run"] += 1
                continue
            if record["status"] != "ok":
                outcome["call failed"] += 1
                continue
            fields = record["app_fields"]
            if fields["status"] == "failed":
                outcome["unreadable answer"] += 1
                continue
            outcome["assessed"] += 1
            if fields.get("esi_level") is None:
                outcome["Cannot assess"] += 1
            if fields["status"] == "needs_review":
                outcome["needs review (any reason)"] += 1
            if NUMBER_FLAG in (fields.get("review_reason") or ""):
                outcome["needs review: handoff number not in reports"] += 1
            level = ai_level(record)
            if level is not None:
                pairs.append((case["reference"]["level"], level))
                if dataset != "purav":
                    rule_pairs.append((case["reference"]["level"], rule_baseline(case)))
                if "nurse_ktas" in case["meta"]:
                    nurse_pairs.append((case["reference"]["level"], case["meta"]["nurse_ktas"]))
        # Every reference 1-2 case, including those without an AI level (Cannot assess shows as Needs review).
        urgent = [(c, r) for c, r in primary if c["reference"]["level"] <= 2]
        urgent_outcome = Counter("given 1-2" if (ai_level(r) or 9) <= 2 else "given 3-5" if ai_level(r) else "no level"
                                 for _, r in urgent)
        # Human comparison and rule on every case in the set, not only those the AI assessed.
        nurse_all = [(c["reference"]["level"], c["meta"]["nurse_ktas"]) for c in chosen if "nurse_ktas" in c["meta"]]
        rule_all = [(c["reference"]["level"], rule_baseline(c)) for c in chosen] if dataset != "purav" else []
        majority = Counter(c["reference"]["level"] for c in chosen).most_common(1)[0]
        results[f"{dataset}/{subset}"] = {
            "label": LABELS[(dataset, subset)],
            "cases": len(chosen),
            "reference_levels": dict(sorted(Counter(c["reference"]["level"] for c in chosen).items())),
            "outcomes": dict(outcome),
            "ai_gave_a_level": wilson(len(pairs), len(chosen)),
            "reference_1_2_all_cases": {key: wilson(urgent_outcome[key], len(urgent))
                                        for key in ("given 1-2", "given 3-5", "no level")},
            "ai_vs_reference": agreement(pairs),
            "ai_levels": dict(sorted(Counter(p for _, p in pairs).items())),
            "simple_rule_vs_reference": agreement(rule_all) if rule_all else None,
            "majority_class_exact": wilson(majority[1], len(chosen)) | {"level": majority[0]},
            # The same guess on exactly the cases the AI gave a level for, so the two rates share a denominator.
            "majority_class_exact_on_ai_assessed": wilson(sum(r == majority[0] for r, _ in pairs), len(pairs))
            | {"level": majority[0]},
            "distinct_patients": len({c["meta"]["source_row"]["subject_id"] for c in chosen})
            if dataset == "mimic" else None,
            "nurse_vs_reference": agreement(nurse_all) if nurse_all else None,
            "nurse_vs_reference_on_ai_assessed": agreement(nurse_pairs) if nurse_pairs else None,
        }
    # Every recorded pair, for audit and SAS.
    for (dataset, case_id, repeat), record in sorted(by_key.items()):
        case = cases[dataset + "_by_id"][case_id]
        fields = record.get("app_fields") or {}
        pairs_rows.append({
            "dataset": dataset, "case_id": case_id, "repeat": repeat,
            "ems_arrival": int("ems" in case["subset"]), "reference_scale": case["reference"]["scale"],
            "reference_level": case["reference"]["level"], "ai_esi": ai_level(record) or "",
            "ai_status": fields.get("status", record["status"]), "risk_score": fields.get("risk_score", ""),
            "nurse_ktas": case["meta"].get("nurse_ktas", ""), "simple_rule": rule_baseline(case) if dataset != "purav" else "",
            "number_flag": int(NUMBER_FLAG in (fields.get("review_reason") or "")),
            "attempts": attempts[(dataset, case_id, repeat)],
            "latency_ms": (record.get("usage") or {}).get("latency_ms", ""),
            "input_tokens": (record.get("usage") or {}).get("input_tokens", ""),
            "output_tokens": (record.get("usage") or {}).get("output_tokens", ""),
            "esi_reason": fields.get("esi_reason", ""),
        })
    return results, pairs_rows, by_key


def consistency(cases, by_key):
    """Same case, same input, repeated calls: how often the suggested ESI is identical."""
    out = {}
    for dataset in ("purav", "mimic", "ktas"):
        repeats = defaultdict(dict)
        for (d, case_id, repeat), record in by_key.items():
            if d == dataset:
                repeats[case_id][repeat] = ai_level(record)
        multi = {cid: r for cid, r in repeats.items() if len(r) > 1}
        if not multi:
            continue
        complete = {cid: r for cid, r in multi.items() if all(v is not None for v in r.values())}
        identical = sum(len(set(r.values())) == 1 for r in complete.values())
        spread_le_1 = sum(max(r.values()) - min(r.values()) <= 1 for r in complete.values())
        r1_r2 = [(r[1], r[2]) for r in complete.values() if 1 in r and 2 in r]
        exact_by_repeat = {}
        for repeat in sorted({k for r in complete.values() for k in r}):
            pairs = [(cases[dataset + "_by_id"][cid]["reference"]["level"], r[repeat]) for cid, r in complete.items()
                     if repeat in r]
            exact_by_repeat[repeat] = agreement(pairs)["exact"]
        out[dataset] = {"cases_repeated": len(multi), "cases_all_repeats_usable": len(complete),
                        "repeats_per_case": sorted(Counter(len(r) for r in multi.values()).items()),
                        "identical_esi_all_repeats": wilson(identical, len(complete)),
                        "esi_within_one_all_repeats": wilson(spread_le_1, len(complete)),
                        "kappa_quadratic_repeat1_vs_repeat2": kappa(r1_r2, "quadratic"),
                        "exact_agreement_by_repeat": exact_by_repeat}
    # Same patients, numbers in digits vs spoken as words.
    pairs = []
    for case in cases["ktas_spoken"]:
        a, b = ai_level(by_key.get(("ktas", case["case_id"], 1))), ai_level(by_key.get(("ktas_spoken", case["case_id"], 1)))
        if a is not None and b is not None:
            pairs.append((a, b))
    out["digits_vs_spoken_words"] = {"pairs": len(pairs), "identical": wilson(sum(a == b for a, b in pairs), len(pairs)),
                                     "within_one": wilson(sum(abs(a - b) <= 1 for a, b in pairs), len(pairs))}
    return out


def flagged_numbers(cases, by_key):
    """Unmodified real outputs whose handoff or reasons name a number the reports don't state."""
    rows = []
    for (dataset, case_id, repeat), record in sorted(by_key.items()):
        if record["status"] == "ok" and record.get("unsupported_numbers"):
            fields = record["app_fields"]
            rows.append({"dataset": dataset, "case_id": case_id, "repeat": repeat,
                         "unsupported_numbers": " ".join(record["unsupported_numbers"]),
                         "summary": fields.get("summary"), "esi_reason": fields.get("esi_reason"),
                         "risk_reason": fields.get("risk_reason"),
                         "report": cases[dataset + "_by_id"][case_id]["input"]["current_transcript"]})
    return rows


def guardrail_mutations(cases, by_key, extractions):
    numbers = []
    fabricated = []
    for (dataset, case_id, repeat), record in sorted(by_key.items()):
        if repeat != 1 or record["status"] != "ok" or record["app_fields"]["status"] == "failed":
            continue
        inputs = {"input": cases[dataset + "_by_id"][case_id]["input"]}
        fields = record["app_fields"]
        for mutant in guardrails.number_mutants(fields, inputs):
            numbers.append({"dataset": dataset, "case_id": case_id, **mutant})
        fabricated.append(guardrails.fabricated_vital(fields, inputs))
    extraction = []
    for (dataset, case_id, repeat), record in sorted(latest(extractions).items()):
        if record["status"] in ("ok",) and record.get("output"):
            info = extraction_info(cases[dataset + "_by_id"][case_id])
            for mutant in guardrails.extraction_mutants(record["output"], info):
                extraction.append({"dataset": dataset, "case_id": case_id, **mutant})
    clean = [m for m in numbers if not m["collision"]]
    collided = [m for m in numbers if m["collision"]]
    by_kind = defaultdict(list)
    for m in extraction:
        by_kind[m["kind"]].append(m["caught"])
    return {
        "handoff_number_check": {
            "mutants": len(numbers),
            "caught_all": wilson(sum(m["caught"] for m in numbers), len(numbers)),
            "caught_when_wrong_value_not_in_report": wilson(sum(m["caught"] for m in clean), len(clean)),
            "wrong_value_also_in_report (undetectable by design)": wilson(len(collided), len(numbers)),
            "caught_when_collides": wilson(sum(m["caught"] for m in collided), len(collided)),
            "fabricated_reading_appended_caught": wilson(sum(fabricated), len(fabricated)),
        },
        "extraction_quote_check": {kind: wilson(sum(v), len(v)) for kind, v in sorted(by_kind.items())},
    }, numbers


def extraction_accuracy(cases, extractions):
    """Extracted vital signs against the values the report states (structured datasets only)."""
    per_vital = defaultdict(Counter)
    statuses = Counter()
    errors = []
    for (dataset, case_id, repeat), record in sorted(latest(extractions).items()):
        case = cases[dataset + "_by_id"][case_id]
        truth = case["truth_vitals"] or {}
        statuses[(dataset, record["status"])] += 1
        facts = [f for f in (record.get("facts") or []) if f["finding_type"] == "vital"]
        if record["status"] != "ok":
            facts = []  # the app stores nothing from a failed run
        got = defaultdict(set)
        for fact in facts:
            got[fact["vital_name"]].add(float(fact["vital_value"]))
        for name in set(truth) | set(got):
            want = truth.get(name)
            tolerance = 0.15 if name == "temp_c" else 0
            correct = {v for v in got[name] if want is not None and abs(v - want) <= tolerance}
            wrong = got[name] - correct
            c = per_vital[(dataset, name)]
            c["stated"] += want is not None
            c["found_correct"] += bool(correct)
            c["extracted_values"] += len(got[name])
            c["wrong_values"] += len(wrong)
            if wrong or (want is not None and not correct):
                errors.append({"dataset": dataset, "case_id": case_id, "vital": name, "stated": want,
                               "extracted": " ".join(map(str, sorted(got[name]))), "run_status": record["status"],
                               "report": case["input"]["current_transcript"]})
    summary = {}
    for dataset in sorted({d for d, _ in per_vital}):
        totals = Counter()
        rows = {}
        for (d, name), c in sorted(per_vital.items()):
            if d != dataset:
                continue
            totals.update(c)
            rows[name] = {"recall": wilson(c["found_correct"], c["stated"]),
                          "precision": wilson(c["extracted_values"] - c["wrong_values"], c["extracted_values"])}
        summary[dataset] = {
            "runs": {status: n for (d, status), n in statuses.items() if d == dataset},
            "recall_all_vitals": wilson(totals["found_correct"], totals["stated"]),
            "precision_all_vitals": wilson(totals["extracted_values"] - totals["wrong_values"], totals["extracted_values"]),
            "per_vital": rows,
        }
    return summary, errors


def operations(records, run_infos):
    out = {}
    price = next((r["price_per_million_tokens"] for r in run_infos if r.get("price_per_million_tokens")), None)
    for kind, recs in records.items():
        ok = [r for r in recs if r["status"] != "error" and r.get("usage")]
        latency = [r["usage"]["latency_ms"] for r in ok]
        tokens_in = [r["usage"]["input_tokens"] for r in ok if r["usage"].get("input_tokens") is not None]
        tokens_out = [r["usage"]["output_tokens"] for r in ok if r["usage"].get("output_tokens") is not None]
        cost = None
        if price and tokens_in and tokens_out:
            cost = (sum(tokens_in) * price[0] + sum(tokens_out) * price[1]) / 1e6
        out[kind] = {"attempts": len(recs), "errors": sum(r["status"] == "error" for r in recs),
                     "latency_ms_median": percentile(latency, 0.5), "latency_ms_p90": percentile(latency, 0.9),
                     "latency_ms_max": max(latency) if latency else None,
                     "input_tokens_mean": sum(tokens_in) / len(tokens_in) if tokens_in else None,
                     "output_tokens_mean": sum(tokens_out) / len(tokens_out) if tokens_out else None,
                     "list_price_usd_total": cost,
                     "list_price_usd_per_call": cost / len(ok) if cost is not None and ok else None}
    out["price_per_million_tokens_used"] = price
    return out


def write_csv(path: Path, rows: list):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = {}
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        cases[path.stem] = load_cases(path.stem)
        cases[path.stem + "_by_id"] = {c["case_id"]: c for c in cases[path.stem]}
    assessments = read_jsonl(args.raw / "assessments.jsonl")
    extractions = read_jsonl(args.raw / "extractions.jsonl")
    run_infos = read_jsonl(args.raw / "runs.jsonl")

    esi, pairs_rows, by_key = analyse_assessments(cases, assessments)
    mutations, number_mutants = guardrail_mutations(cases, by_key, extractions)
    extraction, extraction_errors = extraction_accuracy(cases, extractions)
    flagged = flagged_numbers(cases, by_key)
    primary_ok = [r for k, r in by_key.items() if k[2] == 1 and r["status"] == "ok"]
    metrics = {
        "inputs": {name: hashlib.sha256((args.raw / name).read_bytes()).hexdigest()
                   for name in ("runs.jsonl", "assessments.jsonl", "extractions.jsonl") if (args.raw / name).exists()},
        "case_files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(CASES_DIR.glob("*.jsonl"))},
        "runs": run_infos,
        "esi": esi,
        "consistency": consistency(cases, by_key),
        "model_unsupported_numbers_all_runs": wilson(
            sum(r["status"] == "ok" and bool(r.get("unsupported_numbers")) for r in by_key.values()), len(by_key)),
        "model_unsupported_numbers_on_real_outputs": wilson(
            sum(bool(r.get("unsupported_numbers")) for r in primary_ok), len(primary_ok)),
        "guardrail_mutation_tests": mutations,
        "extraction": extraction,
        "operations": operations({"assessment": assessments, "extraction": extractions}, run_infos),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(round_floats(metrics), indent=1, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    write_csv(args.out / "esi_pairs.csv", pairs_rows)
    write_csv(args.out / "flagged_numbers.csv", flagged)
    write_csv(args.out / "extraction_errors.csv", extraction_errors)
    write_csv(args.out / "number_check_mutants.csv", number_mutants)
    print(f"Wrote {args.out}/metrics.json, esi_pairs.csv ({len(pairs_rows)} rows), "
          f"flagged_numbers.csv ({len(flagged)}), extraction_errors.csv ({len(extraction_errors)})")


if __name__ == "__main__":
    main()
