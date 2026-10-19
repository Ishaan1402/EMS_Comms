"""V2 model collection: frozen source-only inputs, shared gated execution, v2 scoring.

Planning is offline. No annotations enter a prompt. The v1 runner and contracts
remain unchanged; only its provider-independent scheduling/ledger code is reused.
"""

import argparse
import csv
import html
import json
import sys
import time
from pathlib import Path

from .contracts import CHANGE, ROOT, load_cases, load_json, number, text
from .experiments import build_plan, case_input, digest, execute_plan
from .providers import generate
from .report import save_json, table, write_csv
from .v2 import (ATTENTION, CASE_LABELS, DIRECTION, FORM, GROUPS, VERSIONS,
                 output_errors, rank, save_v2_report, score_v2)

DEFAULT_CONFIG = ROOT / "evals/experiments_v2.json"
ATTENTION_CONTEXT = (
    "Independent hypothetical receiving-ED snapshot. Review incoming reports for planning before arrival; "
    "not treatment order or bed assignment. All patients may receive care concurrently. Existing ED demand, "
    "staff availability, beds, and activation status are not supplied. Do not assume a preparation team or empty bays. "
    "Ranking may be indeterminate or not meaningful; ties and Cannot assess are permitted.")


def awareness_schema_v2():
    properties = {k: {"type": "string"} for k in CASE_LABELS.values()}
    properties["meaningful_change"]["enum"] = list(CHANGE)
    properties["attention_category"]["enum"] = list(ATTENTION)
    properties["change_direction"] = {"anyOf": [
        {"type": "string", "enum": list(DIRECTION)}, {"type": "null"}]}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def attention_schema_v2(ids):
    return {"type": "object", "properties": {
        "order": {"type": "object", "properties": {
            cid: {"anyOf": [{"type": "integer", "enum": [1, 2, 3]},
                            {"type": "string", "enum": ["Cannot assess"]}]} for cid in ids},
            "required": list(ids), "additionalProperties": False},
        "explanation": {"type": "string"}}, "required": ["order", "explanation"], "additionalProperties": False}


def attention_errors(output, ids):
    if not isinstance(output, dict) or set(output) != {"order", "explanation"}:
        return ["output fields do not match v2 attention contract"]
    order = output.get("order")
    if not isinstance(order, dict) or set(order) != set(ids) or any(rank(v) is None for v in order.values()):
        return ["missing/invalid v2 attention ranks"]
    if not isinstance(output.get("explanation"), str) or not output["explanation"].strip():
        return ["missing v2 attention explanation"]
    return []


def build_v2_plan(spec, cases, *, model_ids=None, environ=None):
    if spec.get("dataset_version") != VERSIONS["dataset_version"]:
        raise ValueError("V2 runner requires the versioned pilot_v1 sources")
    if not all(isinstance(v, str) and v.startswith("awareness_form_v2") for v in spec.get("prompt_versions", [])):
        raise ValueError("Use v2 awareness prompt versions, not v1 prompts")
    if not isinstance(spec.get("queue_prompt_version"), str) or not spec["queue_prompt_version"].startswith("attention_form_v2"):
        raise ValueError("Use a v2 attention prompt")
    definitions = [{"queue_id": gid, "case_ids": ids, "resource_context": ATTENTION_CONTEXT} for gid, ids in GROUPS.items()]
    plan, jobs, selected, groups = build_plan(spec, cases, definitions, model_ids=model_ids, environ=environ)
    plan["kind"] = "experiment_plan_v2"
    plan["contract_versions"] = dict(VERSIONS)
    plan["source_inputs"] = {"cases": [case_input(c) for c in selected], "exercises": groups}
    plan["dataset_sha256"] = digest(plan["source_inputs"])
    for config in plan["configs"]:
        config["input_contract_version"] = FORM
    for job in jobs:
        job["schema"] = awareness_schema_v2() if job["task"] == "awareness" else attention_schema_v2([c["case_id"] for c in job["input"]["patients"]])
        # Recompute after replacing the v1 schema; include the full v2 schema.
        nbytes = len((job["system"] + json.dumps(job["input"], ensure_ascii=False) + json.dumps(job["schema"])).encode())
        job["reservation_usd"] = ((4 * nbytes + 4096) * job["model"]["input_usd_per_million"] * 1.25
                                  + spec["max_output_tokens"] * job["model"]["output_usd_per_million"]) / 1_000_000
    plan["conservative_reservation_usd"] = sum(j["reservation_usd"] for j in jobs) * (1 + spec["max_retries"])
    plan["notes"].extend([
        "V2 schemas and attention_v2 scenarios only; no v1 category/rank crosswalk.",
        "Inputs contain canonical source fields only; annotations/reviewer metadata never enter prompts.",
        "Quality metrics require separately approved references; human summary grading remains separate.",
        "Default rates are historical examples. Check model access/settings and rates before opting in."])
    return plan, jobs, selected, groups


def empty_references(cases):
    return {"kind": "clinician_reference_v2", **VERSIONS, "rubric_frozen": False, "global_issues": [],
            "cases": [{"case_id": c["case_id"], **dict.fromkeys(CASE_LABELS.values()), "issues": [],
                       "approved": False} for c in cases], "exercises": []}


def validate_references(refs):
    if refs.get("kind") != "clinician_reference_v2" or any(refs.get(k) != v for k, v in VERSIONS.items()):
        raise ValueError("References must match v2 provenance and contract versions before execution")
    known_cases = {c["case_id"] for c in load_cases()}
    for field, id_key, known in (("cases", "case_id", known_cases), ("exercises", "exercise_id", set(GROUPS))):
        seen = set()
        for record in refs.get(field, []):
            identifier = record.get(id_key)
            if identifier not in known or identifier in seen:
                raise ValueError("Unknown/duplicate v2 reference ID")
            seen.add(identifier)


def convert_outputs(raw):
    """Explicit envelope conversion, not a clinical-label mapping or JSON repair."""
    batch = {"kind": "model_predictions_v2_batch", **VERSIONS, "configs": raw["configs"], "records": [], "exercises": []}
    for task, rows in (("awareness", raw["records"]), ("attention", raw["queues"])):
        for original in rows:
            record = {**original, "provider_status": original["status"]}
            if task == "awareness":
                errors = output_errors(record.get("output")) if record["status"] == "ok" else []
            else:
                gid = record.pop("queue_id")
                record["exercise_id"] = gid
                errors = attention_errors(record.get("output"), GROUPS[gid]) if record["status"] == "ok" else []
            record["schema_errors"] = errors
            if errors:
                record["status"], record["error_code"] = "error", "schema_validation_failed"
            if task == "attention":
                output = record.get("output")
                record["order"] = output["order"] if record["status"] == "ok" else None
                record["explanation"] = output["explanation"] if record["status"] == "ok" else None
                batch["exercises"].append(record)
            else:
                batch["records"].append(record)
    return batch


def report_batch(plan, cases, groups, batch, directory, *, references=None):
    if (batch.get("kind") != "model_predictions_v2_batch" or batch.get("configs") != plan["configs"]
            or any(batch.get(k) != v for k, v in VERSIONS.items())):
        raise ValueError("Collected outputs must match the frozen v2 plan")
    configs = {c["config_id"]: c for c in plan["configs"]}
    seen = set()
    for field, id_key, expected_ids in (("records", "case_id", plan["case_ids"]), ("exercises", "exercise_id", plan["queue_ids"])):
        for record in batch[field]:
            config_id, repeat = record.get("config_id"), record.get("repeat_id")
            key = (field, config_id, repeat, record.get(id_key))
            if (config_id not in configs or not isinstance(repeat, int) or isinstance(repeat, bool)
                    or not 1 <= repeat <= configs[config_id]["repeats"] or record.get(id_key) not in expected_ids or key in seen):
                raise ValueError("Unexpected/duplicate output in v2 batch")
            seen.add(key)
    refs = references if references is not None else empty_references(cases)
    validate_references(refs)
    refs = {**refs, "cases": [r for r in refs.get("cases", []) if r["case_id"] in plan["case_ids"]],
            "exercises": [r for r in refs.get("exercises", []) if r["exercise_id"] in plan["queue_ids"]]}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    rows, links = [], []
    for config in plan["configs"]:
        for repeat in range(1, config["repeats"] + 1):
            one = {"kind": "model_predictions_v2", **VERSIONS, "config": {**config, "repeat_id": repeat},
                   "records": [r for r in batch["records"] if r["config_id"] == config["config_id"] and r["repeat_id"] == repeat],
                   "exercises": [r for r in batch["exercises"] if r["config_id"] == config["config_id"] and r["repeat_id"] == repeat]}
            report = score_v2(cases, refs, one, exercise_ids=plan["queue_ids"])
            name = f"{config['config_id']}.repeat-{repeat}"
            folder = directory / name
            save_v2_report(report, folder)
            save_json(folder / "predictions.json", one)
            with (folder / "comparison.csv").open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            rows.append({"repeat_id": repeat, **row})
            links.append((name, f"{name}/dashboard.html"))
    write_csv(directory / "comparison.csv", rows)
    save_json(directory / "index.json", {"kind": "v2_batch_reports", "contract_versions": VERSIONS,
              "reports": [{"name": name, "path": link} for name, link in links],
              "warning": "Repeats are correlated. No automated clinical winner; summaries need human review."})
    links_html = "".join(f"<li><a href='{html.escape(link, quote=True)}'>{html.escape(name)}</a></li>" for name, link in links)
    (directory / "dashboard.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>V2 model comparison</title>"
        "<style>body{font:16px system-ui;margin:32px}table{display:block;overflow:auto;border-collapse:collapse}td,th{padding:8px;border:1px solid #ccc}</style>"
        "</head><body><h1>V2 model comparison</h1><p>Blank means unavailable, not zero. "
        "Unapproved references do not produce quality scores. Repeats are not independent patients. "
        "No automated clinical winner is selected. Summary factuality requires human review.</p>"
        + table(list(rows[0]), [list(r.values()) for r in rows]) + "<h2>Detailed reports</h2><ul>" + links_html + "</ul></body></html>", encoding="utf-8")


def execute_v2(plan, jobs, cases, groups, directory, *, references=None, generator=generate, **gates):
    if plan.get("kind") != "experiment_plan_v2" or plan.get("contract_versions") != VERSIONS:
        raise ValueError("Frozen v2 plan required")
    if references is not None:
        validate_references(references)
        # Validate before a billable call, but do not approve or alter references.
        selected_refs = {**references, "cases": [r for r in references.get("cases", []) if r["case_id"] in plan["case_ids"]],
                         "exercises": [r for r in references.get("exercises", []) if r["exercise_id"] in plan["queue_ids"]]}
        score_v2(cases, selected_refs, {"kind": "model_predictions_v2", **VERSIONS,
                 "config": plan["configs"][0], "records": [], "exercises": []}, exercise_ids=plan["queue_ids"])
    directory = Path(directory)
    try:
        raw = execute_plan(plan, jobs, cases, groups, directory, generator=generator, **gates)
    except KeyboardInterrupt:
        # The shared runner saves its partial ledger and transport outputs first.
        partial = directory / "predictions.json"
        if partial.exists():
            batch = convert_outputs(load_json(partial))
            save_json(directory / "predictions_v2.json", batch)
            report_batch(plan, cases, groups, batch, directory / "report", references=references)
        raise
    batch = convert_outputs(raw)
    save_json(directory / "predictions_v2.json", batch)
    report_batch(plan, cases, groups, batch, directory / "report", references=references)
    return batch


def main(argv=None):
    parser = argparse.ArgumentParser(description="V2 LLM collection: plan by default; explicit execution gates")
    parser.add_argument("command", choices=("plan", "run", "score"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--models", help="Comma-separated configured model IDs; default all")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--references", type=Path, help="Optional v2 imported/reviewed JSON, never sent to models")
    parser.add_argument("--run", type=Path, help="Previously collected run directory; score only, never calls models")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-synthetic", action="store_true")
    parser.add_argument("--confirm-pricing", action="store_true")
    parser.add_argument("--max-usd", type=float)
    parser.add_argument("--max-requests", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError("Output directory already exists; choose a new run directory")
        cases = load_cases()
        if args.command == "score":
            if args.run is None:
                raise ValueError("score requires --run pointing to a collected v2 run")
            plan = load_json(args.run / "plan.json")
            batch = load_json(args.run / "predictions_v2.json")
            if plan.get("kind") != "experiment_plan_v2" or plan.get("contract_versions") != VERSIONS:
                raise ValueError("Cannot rescore a v1 or incompatible run")
            if digest(plan.get("source_inputs")) != plan.get("dataset_sha256"):
                raise ValueError("Frozen source snapshot hash differs")
            snapshot = plan["source_inputs"]
            canonical = {c["case_id"]: case_input(c) for c in cases}
            if any(c != canonical.get(c.get("case_id")) for c in snapshot["cases"]):
                raise ValueError("Frozen run sources differ from canonical dataset")
            selected = snapshot["cases"]
            if [c["case_id"] for c in selected] != plan["case_ids"]:
                raise ValueError("Frozen source case IDs differ from plan")
            refs = load_json(args.references) if args.references else None
            report_batch(plan, selected, snapshot["exercises"], batch, args.out, references=refs)
            print(f"Rescored saved outputs offline: {args.out.resolve()}")
            return 0
        spec = load_json(args.config)
        ids = args.models.split(",") if args.models else None
        plan, jobs, cases, groups = build_v2_plan(spec, cases, model_ids=ids)
        refs = load_json(args.references) if args.references else None
        if refs is not None:
            validate_references(refs)
        if args.command == "plan" or not args.execute:
            args.out.mkdir(parents=True)
            save_json(args.out / "plan.json", plan)
            print(f"PLAN ONLY: {plan['planned_requests']} requests; {plan['maximum_attempts']} maximum attempts.")
            print(f"Padded token-cost reservation: ${plan['conservative_reservation_usd']:.4f}; not a billing guarantee.")
            print("No inference, endpoint probes, uploads, or model downloads made.")
        else:
            result = execute_v2(plan, jobs, cases, groups, args.out, references=refs,
                consent=args.execute, synthetic=args.confirm_synthetic, confirm_pricing=args.confirm_pricing,
                max_usd=args.max_usd, max_requests=args.max_requests)
            print(f"Collected {len(result['records'])} case and {len(result['exercises'])} attention outputs.")
            print("Quality scores require approved references. Costs are estimates; see run_summary.json.")
        print(f"Created {args.out.resolve()}")
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(f"V2 LLM runner error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
