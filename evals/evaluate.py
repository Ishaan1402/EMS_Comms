"""Score precomputed predictions without contacting any model provider."""

import hashlib
import json
import re
from collections import Counter

from .contracts import CHANGE, PREPARATION, number, output_errors, reference_errors, text
from .metrics import classification_metrics, pairwise_metrics, percentile, ratio


def prediction_hash(record):
    payload = {"status": record.get("status"), "output": record.get("output")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def unique_index(rows, key, description):
    result = {}
    for row in rows:
        value = key(row)
        if value in result:
            raise ValueError(f"Duplicate {description}: {value}")
        result[value] = row
    return result


def evaluate(cases, references, predictions, queue_definitions, reviews=None, *, allow_mock=False):
    ref_kind, pred_kind = references.get("kind"), predictions.get("kind")
    mock = ref_kind == "mock_reference" or pred_kind == "mock_predictions"
    if mock and not (allow_mock and ref_kind == "mock_reference" and pred_kind == "mock_predictions"):
        raise ValueError("Mock data require --allow-mock and matching mock references/predictions")
    if not mock and (ref_kind != "clinician_reference" or pred_kind != "model_predictions"):
        raise ValueError("Unknown reference/prediction provenance")
    if not text(references.get("dataset_version")):
        raise ValueError("Reference dataset_version is required")
    if predictions.get("dataset_version") != references["dataset_version"]:
        raise ValueError("Prediction/reference dataset versions differ")
    known = {c["case_id"] for c in cases}
    refs = unique_index(references.get("cases", []), lambda r: r["case_id"], "reference case")
    if set(refs) - known:
        raise ValueError("Unknown reference case IDs")
    eligible = {cid: r for cid, r in refs.items() if not reference_errors(r)}
    excluded = {cid: reference_errors(refs[cid]) if cid in refs else ["missing reference"]
                for cid in sorted(known - set(eligible))}
    rubric_versions = {r["rubric_version"] for r in eligible.values()}
    if len(rubric_versions) > 1:
        raise ValueError("Approved references use different rubrics; freeze one version per evaluation")

    configs = unique_index(predictions.get("configs", []), lambda c: c["config_id"], "configuration")
    if not configs:
        raise ValueError("At least one configuration is required")
    for conf in configs.values():
        for field in ("config_id", "provider", "model", "prompt_version", "input_contract_version"):
            if not text(conf.get(field)):
                raise ValueError(f"Configuration missing {field}")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", conf["config_id"]):
            raise ValueError("config_id must start with a letter and contain only letters, numbers, _, ., -")
        if not isinstance(conf.get("repeats"), int) or not number(conf.get("repeats"), integer=True, minimum=1):
            raise ValueError("repeats must be a positive integer")

    def run_key(row):
        return row["config_id"], row["case_id"], row["repeat_id"]

    records = unique_index(predictions.get("records", []), run_key, "prediction")
    for (config_id, case_id, repeat), record in records.items():
        if config_id not in configs or case_id not in known:
            raise ValueError("Unknown configuration/case in predictions")
        if not isinstance(repeat, int) or not number(repeat, integer=True, minimum=1) or repeat > configs[config_id]["repeats"]:
            raise ValueError("repeat_id outside configuration's expected repeats")
        if record.get("status") not in ("ok", "error", "timeout", "refusal"):
            raise ValueError("Prediction status must be ok/error/timeout/refusal")
        for field in ("latency_ms", "input_tokens", "output_tokens", "cost_usd", "retry_count"):
            value = record.get(field)
            if value is not None and not number(value, integer=field in ("input_tokens", "output_tokens", "retry_count")):
                raise ValueError(f"Invalid telemetry: {field}")

    definitions = unique_index(queue_definitions, lambda q: q["queue_id"], "queue definition")
    for q in definitions.values():
        if len(set(q["case_ids"])) != len(q["case_ids"]) or set(q["case_ids"]) - known:
            raise ValueError("Queue definition contains duplicate/unknown cases")
    queue_refs = unique_index(references.get("queues", []), lambda q: q["queue_id"], "queue reference")
    queue_predictions = unique_index(predictions.get("queues", []),
                                     lambda q: (q["config_id"], q["queue_id"], q["repeat_id"]), "queue prediction")
    if set(queue_refs) - set(definitions):
        raise ValueError("Unknown reference queue")
    for config_id, queue_id, repeat in queue_predictions:
        if config_id not in configs or queue_id not in definitions or not isinstance(repeat, int) or not number(repeat, integer=True, minimum=1) or repeat > configs[config_id]["repeats"]:
            raise ValueError("Unknown queue prediction or invalid repeat")

    def valid_order(order, expected, *, reference=False):
        if not isinstance(order, dict) or set(order) != set(expected):
            return False
        return all((isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= len(expected))
                   or (reference and v == "Unsure") for v in order.values())

    eligible_queues = {}
    for qid, q in queue_refs.items():
        if (q.get("approved") is True and q.get("status") == "Done"
                and all(text(q.get(k)) for k in ("reviewer_id", "rubric_version", "explanation"))
                and valid_order(q.get("order"), definitions[qid]["case_ids"], reference=True)):
            eligible_queues[qid] = q
    queue_rubrics = {q["rubric_version"] for q in eligible_queues.values()} | rubric_versions
    if len(queue_rubrics) > 1:
        raise ValueError("Queue/case rubric versions differ")

    grades = {}
    if reviews is not None:
        if reviews.get("kind") != ("mock_reviews" if mock else "human_summary_reviews"):
            raise ValueError("Review provenance does not match this evaluation")
        if reviews.get("dataset_version") != references["dataset_version"]:
            raise ValueError("Review dataset version differs")
        grades = unique_index(reviews.get("records", []), run_key, "summary review")
        for key, grade in grades.items():
            if key not in records:
                raise ValueError("Summary review has no matching prediction")
            if grade.get("approved") is not True:
                continue
            if not all(text(grade.get(k)) for k in ("reviewer_id", "rubric_version")):
                raise ValueError("Approved summary review missing reviewer/rubric")
            if rubric_versions and grade["rubric_version"] not in rubric_versions:
                raise ValueError("Summary review uses a different rubric")
            if grade.get("prediction_sha256") != prediction_hash(records[key]):
                raise ValueError("Stale summary review: prediction hash differs")
            for field in ("critical_facts_total", "critical_facts_matched", "claims_total", "unsupported_claims",
                          "temporal_errors", "uncertainty_errors"):
                if not number(grade.get(field), integer=True):
                    raise ValueError(f"Approved summary review missing/invalid {field}")
            if grade["critical_facts_matched"] > grade["critical_facts_total"] or grade["unsupported_claims"] > grade["claims_total"]:
                raise ValueError("Review numerator exceeds denominator")
            if not isinstance(grade.get("consequential_error"), bool):
                raise ValueError("consequential_error must be a boolean")

    results = []
    for config_id, config in configs.items():
        attempts, detail, prep_truth, prep_pred, change_truth, change_pred = [], [], [], [], [], []
        for cid in sorted(known):
            for repeat in range(1, config["repeats"] + 1):
                record = records.get((config_id, cid, repeat))
                errors = output_errors(record.get("output")) if record and record["status"] == "ok" else []
                status = "missing" if not record else "invalid" if errors else record["status"]
                usable = status == "ok"
                attempts.append((record, status))
                pred = record["output"] if usable else {}
                sentinel = f"[{status}]"
                ref = eligible.get(cid)
                if ref:
                    prep_truth.append(ref["preparation_category"])
                    prep_pred.append(pred.get("preparation_category", sentinel))
                    if ref["meaningful_change"] in ("Yes", "No"):
                        change_truth.append(ref["meaningful_change"])
                        change_pred.append(pred.get("meaningful_change", sentinel))
                detail.append({"config_id": config_id, "case_id": cid, "repeat_id": repeat,
                               "status": status, "schema_errors": errors, "reference_eligible": bool(ref),
                               "reference_change": ref.get("meaningful_change") if ref else None,
                               "predicted_change": pred.get("meaningful_change"),
                               "reference_preparation": ref.get("preparation_category") if ref else None,
                               "predicted_preparation": pred.get("preparation_category"),
                               "latency_ms": record.get("latency_ms") if record else None,
                               "summary": pred.get("summary"),
                               "reference_summary": ref.get("reference_summary") if ref else None})
        statuses = Counter(s for _, s in attempts)
        telemetry = {field: [r[field] for r, _ in attempts if r and r.get(field) is not None]
                     for field in ("latency_ms", "cost_usd", "input_tokens", "output_tokens", "retry_count")}
        reviewed = [g for key, g in grades.items() if key[0] == config_id and key[1] in eligible
                    and g.get("approved") is True and records[key]["status"] == "ok"
                    and not output_errors(records[key].get("output"))]
        claims = sum(g["claims_total"] for g in reviewed)
        facts = sum(g["critical_facts_total"] for g in reviewed)
        summary = {"reviewed_responses": len(reviewed),
                   "review_coverage": ratio(len(reviewed), len(eligible) * config["repeats"]),
                   "content_recall": ratio(sum(g["critical_facts_matched"] for g in reviewed), facts),
                   "critical_facts_total": facts,
                   "factual_precision": ratio(claims - sum(g["unsupported_claims"] for g in reviewed), claims),
                   "claims_total": claims,
                   "unsupported_claim_rate": ratio(sum(g["unsupported_claims"] > 0 for g in reviewed), len(reviewed)),
                   "temporal_error_rate": ratio(sum(g["temporal_errors"] > 0 for g in reviewed), len(reviewed)),
                   "uncertainty_error_rate": ratio(sum(g["uncertainty_errors"] > 0 for g in reviewed), len(reviewed)),
                   "consequential_errors": sum(g["consequential_error"] for g in reviewed)}
        queue_details = []
        for qid, q in eligible_queues.items():
            for repeat in range(1, config["repeats"] + 1):
                qp = queue_predictions.get((config_id, qid, repeat))
                valid = bool(qp and valid_order(qp.get("order"), definitions[qid]["case_ids"], reference=True))
                values = pairwise_metrics(q["order"], qp["order"] if valid else {})
                queue_details.append({"queue_id": qid, "repeat_id": repeat, "prediction_valid": valid, **values})
        correct = sum(q["correct_pairs"] for q in queue_details)
        ordered = sum(q["ordered_pairs"] for q in queue_details)
        results.append({"config": config, "independent_reference_cases": len(eligible),
                        "expected_responses": len(attempts), "status_counts": dict(statuses),
                        "usable_response_rate": ratio(statuses["ok"], len(attempts)),
                        "schema_validation_rate": ratio(statuses["ok"], statuses["ok"] + statuses["invalid"]),
                        "error_rate": ratio(len(attempts) - statuses["ok"], len(attempts)),
                        "latency_median_ms": percentile(telemetry["latency_ms"], .5),
                        "latency_p95_ms": percentile(telemetry["latency_ms"], .95),
                        "latency_observations": len(telemetry["latency_ms"]),
                        "observed_cost_usd": sum(telemetry["cost_usd"]) if telemetry["cost_usd"] else None,
                        "cost_observations": len(telemetry["cost_usd"]),
                        "input_tokens": sum(telemetry["input_tokens"]) if telemetry["input_tokens"] else None,
                        "output_tokens": sum(telemetry["output_tokens"]) if telemetry["output_tokens"] else None,
                        "retries": sum(telemetry["retry_count"]) if telemetry["retry_count"] else None,
                        "change": classification_metrics(change_truth, change_pred, ("Yes", "No")),
                        "preparation": classification_metrics(prep_truth, prep_pred, PREPARATION),
                        "summary": summary,
                        "queue": {"independent_reference_queues": len(eligible_queues),
                                  "pairwise_accuracy": ratio(correct, ordered), "correct_pairs": correct,
                                  "ordered_pairs": ordered, "details": queue_details},
                        "case_details": detail})
    return {"mode": "MOCK — software tests only" if mock else "Clinician-reference synthetic pilot",
            "dataset_version": references["dataset_version"], "source": references.get("source"),
            "reference_cases_total": len(known), "approved_reference_cases": len(eligible),
            "excluded_references": excluded, "rubric_versions": sorted(rubric_versions),
            "limitations": ["Not clinical validation. No real patient outcomes or source audio.",
                            "Repeated runs are not independent patients. No confidence intervals are claimed.",
                            "Unknown references are excluded from binary change scoring, not relabeled No.",
                            "Missing/failed/invalid predictions count as incorrect on approved classification references.",
                            "Schema validity is measured among returned ok-status outputs, not all attempts; see usable response rate.",
                            "Prepare now false negatives include abstentions and failed/missing responses.",
                            "Macro-F1 averages classes with reference support; undefined metrics are null.",
                            "Summary quality requires separate approved human reviews; no word-overlap grading.",
                            "Temporal lead time, AUROC and calibration are unavailable for these fixtures."],
            "results": results}
