"""Deterministic, deliberately artificial data to test the pipeline, not medicine."""

from copy import deepcopy

from .contracts import CHANGE, PREPARATION, blank_reference
from .evaluate import prediction_hash


def mock_data(cases, queues):
    refs = []
    for i, case in enumerate(cases):
        ref = blank_reference(case["case_id"])
        ref.update(reference_summary=f"MOCK reference {i}: not a clinical answer.",
                   meaningful_change=CHANGE[i % len(CHANGE)],
                   preparation_category=PREPARATION[i % len(PREPARATION)],
                   explanation_missing_information="MOCK explanation only.", status="Done",
                   reviewer_id="MOCK_REVIEWER", rubric_version="mock_v1", approved=True)
        refs.append(ref)
    queue_refs = [{"queue_id": q["queue_id"], "order": {cid: i + 1 for i, cid in enumerate(q["case_ids"])},
                   "explanation": "MOCK ordering, unrelated to patient needs.", "status": "Done",
                   "reviewer_id": "MOCK_REVIEWER", "rubric_version": "mock_v1", "approved": True} for q in queues]
    if len(queue_refs) > 1:
        queue_refs[1]["order"][queues[1]["case_ids"][1]] = 1  # exercise ties
    configs = [{"config_id": name, "provider": "mock", "model": "no-model",
                "prompt_version": "mock_v1", "input_contract_version": "awareness_v1", "repeats": 3}
               for name in ("mock_perfect", "mock_flawed", "mock_failure")]
    records, queue_predictions, reviews = [], [], []
    for config in configs:
        name = config["config_id"]
        for repeat in range(1, 4):
            for i, ref in enumerate(refs):
                output = {"summary": ref["reference_summary"], "meaningful_change": ref["meaningful_change"],
                          "change_explanation": "MOCK explanation.", "missing_information": "MOCK missing data.",
                          "preparation_category": ref["preparation_category"]}
                record = {"config_id": name, "repeat_id": repeat, "case_id": ref["case_id"],
                          "status": "ok", "latency_ms": 100 + i * 5 + repeat,
                          "input_tokens": 0, "output_tokens": 0, "cost_usd": 0, "retry_count": 0, "output": output}
                if name == "mock_failure":
                    record.update(status="timeout", output=None, error="MOCK timeout")
                elif name == "mock_flawed":
                    if i % 2 == 0:
                        output.update(meaningful_change="No", preparation_category="Routine")
                    if i == 1:
                        output.pop("summary")  # malformed output must not disappear from scoring
                    if i == 2 and repeat == 2:
                        continue  # deliberately missing prediction
                records.append(record)
                if record["status"] == "ok" and "summary" in output:
                    reviews.append({"config_id": name, "case_id": ref["case_id"], "repeat_id": repeat,
                                    "prediction_sha256": prediction_hash(record), "reviewer_id": "MOCK_REVIEWER",
                                    "rubric_version": "mock_v1", "approved": True,
                                    "critical_facts_total": 2, "critical_facts_matched": 1 if name == "mock_flawed" else 2,
                                    "claims_total": 3, "unsupported_claims": int(name == "mock_flawed"),
                                    "temporal_errors": int(name == "mock_flawed"), "uncertainty_errors": 0,
                                    "consequential_error": name == "mock_flawed"})
            if name != "mock_failure":
                for q in queue_refs:
                    order = deepcopy(q["order"])
                    if name == "mock_flawed":
                        order = {cid: 4 - rank for cid, rank in order.items()}
                    queue_predictions.append({"config_id": name, "repeat_id": repeat, "queue_id": q["queue_id"], "order": order})
    references = {"kind": "mock_reference", "dataset_version": "MOCK_v1", "cases": refs, "queues": queue_refs}
    predictions = {"kind": "mock_predictions", "dataset_version": "MOCK_v1", "configs": configs,
                   "records": records, "queues": queue_predictions}
    grades = {"kind": "mock_reviews", "dataset_version": "MOCK_v1", "records": reviews}
    return references, predictions, grades


def raw_report_baseline(cases, dataset_version="pilot_v1"):
    """Verbatim current-report baseline. No clinical inference or risk thresholds."""
    config = {"config_id": "raw_report", "provider": "deterministic", "model": "none",
              "prompt_version": "raw_report_v1", "input_contract_version": "awareness_v1", "repeats": 1}
    return {"kind": "model_predictions", "dataset_version": dataset_version, "configs": [config],
            "records": [{"config_id": "raw_report", "case_id": c["case_id"], "repeat_id": 1,
                         "status": "ok", "cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "retry_count": 0,
                         "output": {"summary": c["current_transcript"],
                                    "meaningful_change": "No earlier report" if c["prior_information"] == "No earlier report provided." else "Unclear",
                                    "change_explanation": "No change inference made by this baseline.",
                                    "missing_information": "Not assessed by this baseline.",
                                    "preparation_category": "Unsure"}} for c in cases], "queues": []}
