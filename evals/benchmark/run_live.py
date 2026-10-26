"""Run the app's own AI steps on the benchmark cases and record every call. Makes paid OpenAI calls.

    .venv/bin/python -m evals.benchmark.run_live --env ../EMS_Comms/.env --out evals/benchmark/results/raw

The assessment is backend.case_assessment's score() -> interpret() -> check_numbers(), the same
code, prompt (awareness_v4), schema and model settings the server uses; extraction is
backend.case_extraction's extract() -> interpret() (extraction_v3). No database is touched.
The plan (which cases, how many repeats) is fixed in PLAN and in PROTOCOL.md.

Each attempt is one JSON line in <out>/assessments.jsonl or <out>/extractions.jsonl with the
exact input sent, the parsed model output, the app's interpretation, tokens, latency and any
error. A rerun skips tasks that already succeeded, so an interrupted run can be resumed
(failed tasks are tried again and every attempt stays in the file).
"""
import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
CASES_DIR = HERE / "cases"

# (dataset, subset, repeats). Repeat 1 of every case is the primary analysis; the others measure
# run-to-run consistency. Fixed before any result was seen (PROTOCOL.md).
ASSESS_PLAN = [("purav", "all", 5), ("mimic", "all", 3), ("ktas", "all", 1), ("ktas", "ems", 3),
               ("ktas_spoken", "all", 1)]
EXTRACT_PLAN = [("mimic", "all", 1), ("ktas", "all", 1), ("ktas_spoken", "all", 1)]
CONCURRENCY = 8  # = backend.case_assessment.assessment_executor's workers, so no call waits for a thread


def load_cases(dataset):
    return [json.loads(line) for line in (CASES_DIR / f"{dataset}.jsonl").read_text(encoding="utf-8").splitlines()]


def tasks(plan):
    """(dataset, case, repeat) for every planned call; a subset's repeats extend the 'all' ones."""
    wanted = {}
    for dataset, subset, repeats in plan:
        for case in load_cases(dataset):
            if subset in case["subset"]:
                key = (dataset, case["case_id"])
                wanted[key] = (case, max(repeats, wanted.get(key, (None, 0))[1]))
    return [(dataset, case, repeat) for (dataset, _), (case, repeats) in wanted.items()
            for repeat in range(1, repeats + 1)]


def done(path: Path) -> set:
    if not path.exists():
        return set()
    return {(r["dataset"], r["case_id"], r["repeat"]) for r in map(json.loads, path.read_text().splitlines())
            if r["status"] != "error"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def extraction_info(case):
    """One radio item holding the report, as read_new_information() builds it for a first report."""
    return {"case_id": case["case_id"], "from_version": 0, "to_version": 1, "earlier": [],
            "new": [{"id": "radio-1", "source": "radio", "text": case["input"]["current_transcript"],
                     "version": 1, "time": None, "segment_id": 1, "update_id": None}]}


async def assess(case_assessment, dataset, case, repeat):
    inputs = {"input": case["input"]}
    record = {"dataset": dataset, "case_id": case["case_id"], "repeat": repeat, "started_at": now()}
    try:
        output, usage = await asyncio.wait_for(case_assessment.score(inputs),
                                               timeout=case_assessment.SCORING_TIMEOUT_SECONDS)
        fields = case_assessment.check_numbers(case_assessment.interpret(output), inputs)
        record.update(status="ok", output=output, app_fields=fields, usage=usage,
                      unsupported_numbers=case_assessment.unsupported_numbers(fields, inputs)
                      if fields.get("status") != "failed" else None)
    except Exception as exc:  # recorded, never silently dropped
        record.update(status="error", error=repr(exc), app_error=case_assessment.describe_error(exc))
    record["completed_at"] = now()
    return record


async def extract(case_extraction, dataset, case, repeat):
    info = extraction_info(case)
    record = {"dataset": dataset, "case_id": case["case_id"], "repeat": repeat, "started_at": now()}
    try:
        output, usage = await asyncio.wait_for(case_extraction.extract(info),
                                               timeout=case_extraction.EXTRACTION_TIMEOUT_SECONDS)
        record.update(output=output, usage=usage)
        try:
            facts = case_extraction.interpret(output, info)
            record.update(status="ok" if facts is not None else "unreadable", facts=facts)
        except case_extraction.UnverifiedSourceQuote:
            record.update(status="unverified_quote", facts=None)  # the app fails the run and stores nothing
    except Exception as exc:
        record.update(status="error", error=repr(exc))
    record["completed_at"] = now()
    return record


async def run_all(kind, step, module, todo, path, max_cost, spent):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    finished = 0

    async def one(task):
        nonlocal finished
        if spent[0] >= max_cost:
            return
        async with semaphore:
            record = await step(module, *task)
        async with lock:
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            usage = record.get("usage") or {}
            spent[0] += module.estimate_cost(MODEL, usage) or 0
            finished += 1
            if finished % 50 == 0 or finished == len(todo):
                print(f"  {kind}: {finished}/{len(todo)} done, about ${spent[0]:.4f} so far", flush=True)

    await asyncio.gather(*(one(task) for task in todo))


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def main(argv=None):
    global MODEL
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, required=True, help=".env holding OPENAI_API_KEY (never printed)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-usd", type=float, default=10.0, help="Stop starting new calls past this estimate")
    parser.add_argument("--only", choices=("assess", "extract"), help="Run one step only")
    parser.add_argument("--limit", type=int, help="At most this many tasks per step (smoke test)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(args.env)  # before backend.openai_config reads the environment
    os.environ["EIR_DB_PATH"] = str(args.out / "unused.db")  # nothing is written; keeps the real DB untouched
    from backend import case_assessment, case_extraction, openai_config
    import openai
    if not openai_config.openai_client:
        sys.exit("OPENAI_API_KEY is not set in that .env")
    MODEL = openai_config.SCORING_MODEL
    if case_extraction.EXTRACTION_MODEL != MODEL:
        print(f"note: extraction model {case_extraction.EXTRACTION_MODEL} differs from scoring model {MODEL}")

    args.out.mkdir(parents=True, exist_ok=True)
    run_info = {
        "started_at": now(), "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
        "scoring_model": MODEL, "extraction_model": case_extraction.EXTRACTION_MODEL,
        "reasoning_effort": openai_config.SCORING_REASONING_EFFORT, "extra_body": openai_config.scoring_extra_body(),
        "assessment_prompt": case_assessment.PROMPT_VERSION,
        "assessment_prompt_sha256": hashlib.sha256(case_assessment.PROMPT.encode()).hexdigest(),
        "extraction_prompt": case_extraction.PROMPT_VERSION,
        "extraction_prompt_sha256": hashlib.sha256(case_extraction.PROMPT.encode()).hexdigest(),
        "price_per_million_tokens": case_assessment.PRICES_PER_MILLION.get(MODEL),
        "openai_sdk": openai.__version__, "python": platform.python_version(), "platform": platform.platform(),
        "concurrency": CONCURRENCY, "assess_plan": ASSESS_PLAN, "extract_plan": EXTRACT_PLAN,
        "case_files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(CASES_DIR.glob("*.jsonl"))},
    }
    with (args.out / "runs.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(run_info, sort_keys=True) + "\n")
    print(json.dumps(run_info, indent=1))

    spent = [0.0]
    started = time.monotonic()
    steps = [("assess", assess, case_assessment, ASSESS_PLAN, "assessments.jsonl"),
             ("extract", extract, case_extraction, EXTRACT_PLAN, "extractions.jsonl")]
    for kind, step, module, plan, name in steps:
        if args.only and args.only != kind:
            continue
        path = args.out / name
        finished = done(path)
        todo = [(d, c, r) for d, c, r in tasks(plan) if (d, c["case_id"], r) not in finished][:args.limit]
        print(f"{kind}: {len(todo)} calls to make ({len(finished)} already recorded)")
        asyncio.run(run_all(kind, step, module, todo, path, args.max_usd, spent))
    print(f"Finished in {time.monotonic() - started:.0f} s, estimated cost ${spent[0]:.4f}")
    if spent[0] >= args.max_usd:
        print("Stopped at --max-usd; rerun with a higher limit to finish the remaining calls")


MODEL = None

if __name__ == "__main__":
    main()
