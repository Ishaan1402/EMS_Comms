"""Offline by default. Only run --execute can make explicitly authorized calls."""

import argparse
import sys
from pathlib import Path

from .annotations import import_workbook
from .contracts import DEFAULT_CASES, ROOT, blank_reference, load_cases, load_json, output_errors
from .demo import mock_data, raw_report_baseline
from .evaluate import evaluate, prediction_hash
from .experiments import build_plan, execute_plan, score_experiment
from .report import save_json, save_report

QUEUES = Path(__file__).with_name("queues.json")


def review_template(predictions):
    return {"kind": "mock_reviews" if predictions["kind"] == "mock_predictions" else "human_summary_reviews",
            "dataset_version": predictions["dataset_version"],
            "records": [{"config_id": r["config_id"], "case_id": r["case_id"], "repeat_id": r["repeat_id"],
                         "prediction_sha256": prediction_hash(r), "reviewer_id": None, "rubric_version": None,
                         "approved": False, "critical_facts_total": None, "critical_facts_matched": None,
                         "claims_total": None, "unsupported_claims": None, "temporal_errors": None,
                         "uncertainty_errors": None, "consequential_error": None}
                        for r in predictions.get("records", []) if r.get("status") == "ok" and not output_errors(r.get("output"))]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="EMS evaluation: offline by default, explicit opt-in for live calls")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "import-annotations", "baseline", "demo", "score", "plan", "run"):
        command = sub.add_parser(name)
        command.add_argument("--cases", type=Path, default=DEFAULT_CASES)
        command.add_argument("--queues", type=Path, default=QUEUES)
        command.add_argument("--out", type=Path, required=True, help="New output directory (never overwritten)")
        if name == "import-annotations":
            command.add_argument("--workbook", type=Path, required=True)
        if name == "baseline":
            command.add_argument("--dataset-version", default="pilot_v1")
        if name == "score":
            command.add_argument("--references", type=Path, required=True)
            command.add_argument("--predictions", type=Path, required=True)
            command.add_argument("--reviews", type=Path)
            command.add_argument("--allow-mock", action="store_true")
        if name in ("plan", "run"):
            command.add_argument("--config", type=Path, default=ROOT / "evals/experiments.json")
            command.add_argument("--models", help="Comma-separated experiment model IDs; default all")
        if name == "run":
            command.add_argument("--execute", action="store_true", help="Actually call models (default: plan only)")
            command.add_argument("--confirm-synthetic", action="store_true", help="Confirm all inputs are fictional; not a PHI detector")
            command.add_argument("--confirm-pricing", action="store_true", help="Confirm configured rates/account billing controls were checked")
            command.add_argument("--max-usd", type=float, help="Finite padded token-cost reservation; not a provider billing cap")
            command.add_argument("--max-requests", type=int, default=1000, help="Maximum attempts, including retries")
            command.add_argument("--references", type=Path, help="Optional approved references; otherwise quality scores are N/A")
    args = parser.parse_args(argv)
    try:
        cases, queues = load_cases(args.cases), load_json(args.queues)
        if args.out.exists():
            raise ValueError("Output directory already exists; choose a new run directory")
        if args.command == "prepare":
            args.out.mkdir(parents=True)
            save_json(args.out / "cases.json", cases)
            save_json(args.out / "references.json", {"kind": "clinician_reference", "dataset_version": "pilot_v1",
                      "cases": [blank_reference(c["case_id"]) for c in cases],
                      "queues": [{"queue_id": q["queue_id"], "order": dict.fromkeys(q["case_ids"]),
                                  "explanation": None, "status": "Not started", "reviewer_id": None,
                                  "rubric_version": None, "approved": False} for q in queues]})
        elif args.command == "import-annotations":
            reference = import_workbook(args.workbook, cases, queues)
            args.out.mkdir(parents=True)
            save_json(args.out / "references.json", reference)
        elif args.command == "baseline":
            args.out.mkdir(parents=True)
            prediction = raw_report_baseline(cases, args.dataset_version)
            save_json(args.out / "predictions.json", prediction)
            save_json(args.out / "summary_reviews.json", review_template(prediction))
        elif args.command == "demo":
            ref, pred, reviews = mock_data(cases, queues)
            result = evaluate(cases, ref, pred, queues, reviews, allow_mock=True)
            save_report(result, args.out)
            save_json(args.out / "mock_references.json", ref)
            save_json(args.out / "mock_predictions.json", pred)
            save_json(args.out / "mock_reviews.json", reviews)
        elif args.command in ("plan", "run"):
            spec = load_json(args.config)
            selected = args.models.split(",") if args.models is not None else None
            plan, jobs, selected_cases, selected_queues = build_plan(spec, cases, queues, model_ids=selected)
            execute = args.command == "run" and args.execute
            if not execute:
                args.out.mkdir(parents=True)
                save_json(args.out / "plan.json", plan)
                print(f"PLAN ONLY: {plan['planned_requests']} requests, {plan['maximum_attempts']} maximum attempts.")
                print(f"Padded token-cost reservation: ${plan['conservative_reservation_usd']:.4f}; not a billing guarantee.")
                print("No model calls, endpoint probes, or model downloads made.")
            else:
                ref = load_json(args.references) if args.references else None
                if ref is not None and (ref.get("kind") != "clinician_reference" or ref.get("dataset_version") != plan["dataset_version"]):
                    raise ValueError("Real reference provenance/version must match the experiment before execution")
                prediction = execute_plan(plan, jobs, selected_cases, selected_queues, args.out,
                                          consent=True, synthetic=args.confirm_synthetic, max_usd=args.max_usd,
                                          max_requests=args.max_requests, confirm_pricing=args.confirm_pricing)
                result = score_experiment(plan, selected_cases, selected_queues, prediction,
                                          args.out / "report", references=ref)
                save_json(args.out / "summary_review_template.json", review_template(prediction))
                print(f"Collected {len(prediction['records'])} case and {len(prediction['queues'])} queue outputs.")
                print(f"Approved references: {result['approved_reference_cases']}/{len(selected_cases)}; summary review still required.")
        else:
            ref, pred = load_json(args.references), load_json(args.predictions)
            reviews = load_json(args.reviews) if args.reviews else None
            result = evaluate(cases, ref, pred, queues, reviews, allow_mock=args.allow_mock)
            save_report(result, args.out)
            save_json(args.out / "summary_review_template.json", review_template(pred))
        print(f"Created {args.out.resolve()}")
        if args.command in ("demo", "score"):
            print(f"{result['mode']}; approved references: {result['approved_reference_cases']}/{len(cases)}")
        elif args.command not in ("plan", "run"):
            print("No clinical performance scores. Review/approve reference answers before scoring.")
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
