"""Plan by default; collect real outputs only with explicit execution consent."""

import hashlib
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .contracts import CASE_FIELDS, ROOT, blank_reference, number, text
from .evaluate import evaluate
from .providers import KEY_ENV, ProviderError, awareness_schema, generate, headers_for, queue_schema, request_spec
from .report import save_json, save_report

PROMPTS = Path(__file__).with_name("prompts")
PROVIDERS = set(KEY_ENV) | {"ollama"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def prompt(version):
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", version):
        raise ValueError("Prompt version must contain only letters, numbers, underscores")
    return (PROMPTS / f"{version}.txt").read_text(encoding="utf-8")


def case_input(case):
    # Never serialize arbitrary fixture columns: future annotation fields must not leak.
    return {key: case[key] for key in CASE_FIELDS}


def build_plan(spec, cases, queues, *, model_ids=None, environ=None):
    environ = os.environ if environ is None else environ
    allowed_spec = {"dataset_version", "purpose", "repeats", "prompt_versions", "queue_prompt_version",
                    "max_output_tokens", "timeout_seconds", "max_retries", "random_seed", "pricing_checked",
                    "models", "case_ids", "queue_ids", "tuning_encounter_ids"}
    if not isinstance(spec, dict) or set(spec) - allowed_spec:
        raise ValueError("Unknown experiment fields; credentials belong only in environment variables")
    if not text(spec.get("dataset_version")) or spec.get("purpose") not in ("development", "holdout"):
        raise ValueError("Specify dataset_version and purpose=development/holdout")
    for field, maximum in (("repeats", 20), ("max_output_tokens", 8192), ("timeout_seconds", 60), ("max_retries", 2)):
        value = spec.get(field)
        if not isinstance(value, int) or not number(value, integer=True, minimum=0 if field == "max_retries" else 1) or value > maximum:
            raise ValueError(f"Invalid experiment {field}")
    if not isinstance(spec.get("random_seed"), int) or isinstance(spec["random_seed"], bool):
        raise ValueError("random_seed must be an integer")
    versions = spec.get("prompt_versions")
    if not isinstance(versions, list) or not versions or len(set(versions)) != len(versions):
        raise ValueError("Use a nonempty list of unique prompt_versions")
    prompts = {v: prompt(v) for v in versions}
    queue_version = spec.get("queue_prompt_version")
    prompts[queue_version] = prompt(queue_version)
    known = {c["case_id"] for c in cases}
    selected = spec.get("case_ids")
    if selected is not None and (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or set(selected) - known):
        raise ValueError("case_ids must be unique known IDs")
    cases = [c for c in cases if selected is None or c["case_id"] in selected]
    if spec["purpose"] == "holdout":
        tuning = spec.get("tuning_encounter_ids")
        if not isinstance(tuning, list) or not all(isinstance(cid, str) and cid for cid in tuning):
            raise ValueError("Holdout requires explicit tuning_encounter_ids; include every encounter seen during prompt tuning")
        if set(tuning) & {c["encounter_id"] for c in cases}:
            raise ValueError("Holdout leakage: an encounter also appears in the tuning set")
    selected_cases = {c["case_id"] for c in cases}
    known_queues = {q["queue_id"] for q in queues}
    queue_ids = spec.get("queue_ids")
    if queue_ids is not None and (not isinstance(queue_ids, list) or len(set(queue_ids)) != len(queue_ids) or set(queue_ids) - known_queues):
        raise ValueError("queue_ids must be unique known queue IDs")
    queues = [q for q in queues if (queue_ids is None or q["queue_id"] in queue_ids) and set(q["case_ids"]) <= selected_cases]
    if queue_ids is not None and set(queue_ids) != {q["queue_id"] for q in queues}:
        raise ValueError("Requested queues include patients outside the selected cases")
    if model_ids is not None and len(set(model_ids)) != len(model_ids):
        raise ValueError("Duplicate selected model IDs")
    models = spec.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("At least one model is required")
    ids = [m.get("id") for m in models]
    if len(set(ids)) != len(ids) or (model_ids is not None and set(model_ids) - set(ids)):
        raise ValueError("Duplicate or unknown model IDs")
    models = [m for m in models if model_ids is None or m["id"] in model_ids]
    if not models:
        raise ValueError("No selected models")
    configs, jobs = [], []
    source = {c["case_id"]: c for c in cases}
    for model in models:
        allowed_model = {"id", "provider", "model", "base_url", "think", "temperature", "context_tokens",
                         "reasoning_effort", "thinking_level", "input_usd_per_million", "output_usd_per_million"}
        if set(model) - allowed_model:
            raise ValueError("Unknown model fields; never place API keys in experiment JSON")
        if not isinstance(model["id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", model["id"]):
            raise ValueError("Model IDs must be safe identifiers")
        if model.get("provider") not in PROVIDERS or not isinstance(model.get("model"), str) or not text(model["model"]):
            raise ValueError("Invalid provider/model")
        for key in ("input_usd_per_million", "output_usd_per_million"):
            if not number(model.get(key)):
                raise ValueError(f"Missing/invalid {key}; unknown pricing is not zero")
        if model["provider"] == "ollama" and (model["input_usd_per_million"] or model["output_usd_per_million"]):
            raise ValueError("Loopback Ollama has zero API token fees; compute is a separate cost")
        # Validate endpoint without making a request or needing a key.
        request_spec(model, "", "", awareness_schema(), spec["max_output_tokens"])
        for version in versions:
            config = {"config_id": f"{model['id']}.{version}", "provider": model["provider"], "model": model["model"],
                      "prompt_version": version, "prompt_sha256": hashlib.sha256(prompts[version].encode()).hexdigest(),
                      "queue_prompt_version": queue_version, "queue_prompt_sha256": hashlib.sha256(prompts[queue_version].encode()).hexdigest(),
                      "input_contract_version": "awareness_v1", "repeats": spec["repeats"],
                      "generation_settings": {k: v for k, v in model.items() if k not in ("id", "provider", "model")}}
            configs.append(config)
            for repeat in range(1, spec["repeats"] + 1):
                for case in cases:
                    jobs.append({"config_id": config["config_id"], "repeat_id": repeat,
                                 "case_id": case["case_id"], "task": "awareness", "model": model,
                                 "system": prompts[version], "input": case_input(case), "schema": awareness_schema()})
                for queue in queues:
                    jobs.append({"config_id": config["config_id"], "repeat_id": repeat,
                                 "queue_id": queue["queue_id"], "task": "queue", "model": model,
                                 "system": prompts[queue_version], "input": {"resource_context": queue["resource_context"],
                                 "patients": [case_input(source[cid]) for cid in queue["case_ids"]]},
                                 "schema": queue_schema(queue["case_ids"])})
    for job in jobs:
        # Deliberately padded estimate, not a provider billing guarantee/tokenizer.
        nbytes = len((job["system"] + json.dumps(job["input"], ensure_ascii=False) + json.dumps(job["schema"])).encode())
        job["reservation_usd"] = ((4 * nbytes + 4096) * job["model"]["input_usd_per_million"] * 1.25
                                  + spec["max_output_tokens"] * job["model"]["output_usd_per_million"]) / 1_000_000
    random.Random(spec["random_seed"]).shuffle(jobs)
    plan = {"kind": "experiment_plan", "dataset_version": spec["dataset_version"], "purpose": spec["purpose"],
            "created_at": datetime.now(timezone.utc).isoformat(), "spec": spec, "configs": configs,
            "case_ids": [c["case_id"] for c in cases], "queue_ids": [q["queue_id"] for q in queues],
            "dataset_sha256": digest({"cases": [case_input(c) for c in cases], "queues": queues}),
            "prompts": prompts, "planned_requests": len(jobs),
            "maximum_attempts": len(jobs) * (1 + spec["max_retries"]),
            "conservative_reservation_usd": sum(j["reservation_usd"] for j in jobs) * (1 + spec["max_retries"]),
            "key_presence": {p: bool(environ.get(env)) for p, env in KEY_ENV.items() if any(m["provider"] == p for m in models)},
            "notes": ["PLAN ONLY. No inference, endpoint probes, uploads, or downloads.",
                      "Reservation is a padded estimate, not a hard provider-side billing cap.",
                      "Ollama token fees are zero; hardware/electricity/hosting are not free.",
                      "Provider aliases and local tags can change; pin snapshots/digests for final experiments.",
                      "Holdout overlap checks rely on declared tuning encounter IDs; they cannot audit undeclared prior exposure.",
                      "Queue calls use a fixed queue prompt; awareness prompt changes do not affect their inputs."]}
    return plan, jobs, cases, queues


def execute_plan(plan, jobs, cases, queues, directory, *, consent=False, synthetic=False,
                 max_usd=None, max_requests=1000, confirm_pricing=False,
                 generator=generate, environ=None, sleep=time.sleep):
    """No network until all gates pass. Injected generators make tests completely offline."""
    if not consent or not synthetic:
        raise ValueError("Execution requires --execute and --confirm-synthetic; planning never calls a model")
    if not number(max_usd) or not isinstance(max_requests, int) or isinstance(max_requests, bool) or max_requests < 1:
        raise ValueError("Set a finite --max-usd and positive --max-requests")
    if plan["maximum_attempts"] > max_requests:
        raise ValueError("Planned attempts exceed --max-requests; reduce models/repeats or increase explicitly")
    commercial = any(j["model"]["provider"] != "ollama" for j in jobs)
    if commercial and (not confirm_pricing or not max_usd):
        raise ValueError("Commercial APIs require --confirm-pricing and a positive --max-usd, even with credits")
    if plan["conservative_reservation_usd"] > max_usd:
        raise ValueError("Padded reservation exceeds --max-usd; no requests made")
    environ = os.environ if environ is None else environ
    for job in jobs:
        headers_for(job["model"]["provider"], environ)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / "plan.json", plan)
    predictions = {"kind": "model_predictions", "dataset_version": plan["dataset_version"],
                   "configs": plan["configs"], "records": [], "queues": [],
                   "experiment": {"purpose": plan["purpose"], "dataset_sha256": plan["dataset_sha256"]}}
    calls, reserved, estimated, unknown = 0, 0.0, 0.0, 0
    stopped = None
    try:
        with (directory / "attempts.jsonl").open("x", encoding="utf-8") as ledger:
            for job in jobs:
                started = time.monotonic()
                attempt_rows = []
                for retry in range(plan["spec"]["max_retries"] + 1):
                    if calls >= max_requests or reserved + job["reservation_usd"] > max_usd:
                        stopped = "budget_or_request_limit"
                        break
                    calls += 1
                    reserved += job["reservation_usd"]
                    retryable = False
                    try:
                        response = generator(job["model"], job["system"], json.dumps(job["input"], ensure_ascii=False),
                                             job["schema"], plan["spec"]["max_output_tokens"],
                                             plan["spec"]["timeout_seconds"], environ=environ)
                    except ProviderError as exc:
                        response = {"status": exc.status, "output": None, "error_code": exc.code,
                                    "input_tokens": None, "output_tokens": None}
                        retryable = exc.retryable
                    except (KeyError, TypeError, AttributeError, ValueError):
                        response = {"status": "error", "output": None, "error_code": "adapter_response_error",
                                    "input_tokens": None, "output_tokens": None}
                    inp, out = response.get("input_tokens"), response.get("output_tokens")
                    cost = 0 if job["model"]["provider"] == "ollama" else (
                        (inp * job["model"]["input_usd_per_million"] * 1.25
                         + out * job["model"]["output_usd_per_million"]) / 1_000_000
                        if number(inp, integer=True) and number(out, integer=True) else None)
                    unknown += int(cost is None)
                    estimated += cost or 0
                    row = {"config_id": job["config_id"], "repeat_id": job["repeat_id"],
                           "case_id": job.get("case_id"), "queue_id": job.get("queue_id"),
                           "task": job["task"], "attempt": retry + 1, **response,
                           "estimated_token_cost_usd": cost, "reservation_usd": job["reservation_usd"]}
                    ledger.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    ledger.flush()
                    attempt_rows.append(row)
                    if cost is not None and cost > job["reservation_usd"]:
                        stopped = "cost_exceeded_reservation"
                        break
                    if not retryable or retry == plan["spec"]["max_retries"]:
                        break
                    sleep(min(2 ** retry, 4))
                if attempt_rows:
                    last = attempt_rows[-1]
                    record = {"config_id": job["config_id"], "repeat_id": job["repeat_id"],
                              "status": last["status"], "output": last.get("output"),
                              "raw_text": last.get("raw_text"), "error_code": last.get("error_code"),
                              "actual_model": last.get("actual_model"), "finish_reason": last.get("finish_reason"),
                              "latency_ms": (time.monotonic() - started) * 1000, "retry_count": len(attempt_rows) - 1,
                              "input_sha256": digest(job["input"]),
                              "cost_usd": sum(r["estimated_token_cost_usd"] for r in attempt_rows)
                                  if all(r["estimated_token_cost_usd"] is not None for r in attempt_rows) else None,
                              "cost_basis": "padded list-rate token estimate; not invoice", "usage": last.get("usage"),
                              "input_tokens": sum(r["input_tokens"] for r in attempt_rows)
                                  if all(number(r.get("input_tokens"), integer=True) for r in attempt_rows) else None,
                              "output_tokens": sum(r["output_tokens"] for r in attempt_rows)
                                  if all(number(r.get("output_tokens"), integer=True) for r in attempt_rows) else None}
                    if job["task"] == "awareness":
                        record["case_id"] = job["case_id"]
                        predictions["records"].append(record)
                    else:
                        record["queue_id"] = job["queue_id"]
                        output = record["output"]
                        valid = isinstance(output, dict) and set(output) == {"order", "explanation"} and isinstance(output.get("explanation"), str) and bool(output["explanation"].strip())
                        record["order"] = output.get("order") if valid and record["status"] == "ok" else None
                        record["explanation"] = output.get("explanation") if valid else None
                        predictions["queues"].append(record)
                if stopped:
                    break
    except KeyboardInterrupt:
        unknown += 1
        stopped = "interrupted; in-flight billing/response may be unknown"
        raise
    finally:
        # Exclusive final writes preserve a partial run if interrupted. Missing jobs stay missing.
        save_json(directory / "predictions.json", predictions)
        save_json(directory / "run_summary.json", {"attempts": calls, "reserved_usd": reserved,
                  "known_estimated_token_cost_usd": estimated, "unknown_cost_attempts": unknown,
                  "stopped": stopped, "cost_scope": "all awareness and queue calls including retries",
                  "cost_warning": "Estimates are not invoices. Provider account billing limits are still required."})
    return predictions


def score_experiment(plan, cases, queues, predictions, directory, *, references=None, reviews=None):
    if references is None:
        references = {"kind": "clinician_reference", "dataset_version": plan["dataset_version"],
                      "cases": [blank_reference(c["case_id"]) for c in cases], "queues": []}
    elif set(plan["case_ids"]) != {c["case_id"] for c in cases}:
        raise ValueError("Scoring cases differ from the frozen experiment plan")
    # Filter references to the explicitly selected experiment subset, without changing approvals.
    references = {**references, "cases": [r for r in references.get("cases", []) if r["case_id"] in plan["case_ids"]],
                  "queues": [r for r in references.get("queues", []) if r["queue_id"] in plan["queue_ids"]]}
    report = evaluate(cases, references, predictions, queues, reviews)
    report["experiment_purpose"] = plan["purpose"]
    report["limitations"].append("Live-run costs are padded token estimates, not invoices. Case cost metrics exclude queue calls; run_summary.json includes both.")
    report["limitations"].append("Development comparisons can tune prompts; they do not establish a held-out winner. Human summary review is still required.")
    save_report(report, directory)
    return report
