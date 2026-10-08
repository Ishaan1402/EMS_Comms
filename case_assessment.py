"""AI assessment of EMS cases, on the evaluation contract (evals/contracts.py).

The model gets the same system prompt (evals/prompts/awareness_v2.txt) and the same input
fields as the evaluation harness, and must answer with the same five fields, so what the
evaluation measures is what the app runs:
    summary, meaningful_change, change_explanation, missing_information, preparation_category

"Earlier" information is what the hospital had acknowledged before this assessment, so
meaningful_change answers "what changed since the hospital last looked". Before any
acknowledgment everything is current, as in a first report.

Every attempt is stored in risk_assessments with the case info_version it read, so:
- a failure never overwrites earlier information or the last usable assessment;
- a slow result for older information can't replace a newer one (the current
  assessment is the usable one with the highest based_on_version);
- unusable output is marked needs_review with a reason, never replaced by a default.

Runs in-process (like realtime.py), so use a single server worker.
"""
import asyncio
import concurrent.futures
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import openai

from database import get_db, query, run, utc_now_iso
from routes import recordings

PROMPT_VERSION = "awareness_v2"
PROMPT = (Path(__file__).parent / "evals" / "prompts" / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")

# The evaluation contract, kept identical to evals/contracts.py (test_case_model checks this).
CHANGE = ("Yes", "No", "Unclear", "No earlier report")
PREPARATION = ("Prepare now", "Can wait", "Routine", "Unsure")
OUTPUT_FIELDS = ("summary", "meaningful_change", "change_explanation", "missing_information", "preparation_category")
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        **{field: {"type": "string"} for field in OUTPUT_FIELDS},
        "meaningful_change": {"type": "string", "enum": list(CHANGE)},
        "preparation_category": {"type": "string", "enum": list(PREPARATION)},
    },
    "required": list(OUTPUT_FIELDS),
    "additionalProperties": False,
}
# Wording of pilot_cases.json for a case with no earlier report.
NO_EARLIER_REPORT = "No earlier report provided."

# The prompt's "Unsure" is the older wording; the app stores and shows "Cannot assess".
CANNOT_ASSESS = "Cannot assess"
CANNOT_ASSESS_REASON = "Insufficient information to assess preparation needs."
UNREADABLE_ERROR = "the AI's answer didn't follow the expected format"

# List prices in USD per million tokens (input, output), checked 2026-10-07. A model missing
# here gets no cost estimate rather than a wrong one.
PRICES_PER_MILLION = {"gpt-6-luna": (0.10, 0.50)}

# Longer than the request timeout times its attempts (recordings.SCORING_REQUEST_TIMEOUT_SECONDS,
# OPENAI_MAX_RETRIES), so the worker thread is free again by the time a call is given up on.
SCORING_TIMEOUT_SECONDS = 90
# Own pool, so assessments never queue behind the older recording flow's calls.
assessment_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="assess")
# Wait this long before assessing, and again before re-assessing information that arrived
# during a run, so a report that arrives as several segments is assessed once, whole.
SETTLE_SECONDS = 2.0

ASSESSMENT_COLUMNS = (
    "id, case_id, based_on_version, baseline_version, status, preparation_category, meaningful_change, "
    "summary, change_explanation, missing_information, review_reason, error, scorer_version, "
    "input_tokens, output_tokens, latency_ms, cost_usd, started_at, completed_at, updated_at"
)


def scorer_version() -> str:
    """Model and prompt that produced an assessment, stored with it."""
    return f"{recordings.SCORING_MODEL}/{PROMPT_VERSION}"


# Vital signs an EMT can enter: name -> (label, unit, min, max).
VITAL_SIGNS = {
    "hr": ("HR", "bpm", 0, 300),
    "sbp": ("Systolic BP", "mmHg", 0, 300),
    "dbp": ("Diastolic BP", "mmHg", 0, 250),
    "rr": ("RR", "/min", 0, 100),
    "spo2": ("SpO2", "%", 0, 100),
    "temp_c": ("Temp", "°C", 20, 45),
    "gcs": ("GCS", "", 3, 15),
    "glucose": ("Glucose", "mg/dL", 0, 2000),
}


def format_vitals(readings: list) -> str:
    """e.g. "HR 110 bpm, SpO2 91 %" in VITAL_SIGNS order."""
    values = {r["name"]: r["value"] for r in readings}
    parts = []
    for name, (label, unit, _, _) in VITAL_SIGNS.items():
        if name in values:
            value = values[name]
            number = int(value) if float(value).is_integer() else value
            parts.append(f"{label} {number} {unit}".strip())
    return ", ".join(parts)


class ScoringUnavailable(Exception):
    pass


class ScoringRefused(Exception):
    pass


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format(items: list) -> str:
    """Radio transcript segments run together as speech (they're cut mid-sentence); typed entries get a line each."""
    paragraphs, speech = [], []
    for _, _, _, text, typed in items:
        if typed:
            if speech:
                paragraphs.append(" ".join(speech))
                speech = []
            paragraphs.append(text)
        else:
            speech.append(text)
    if speech:
        paragraphs.append(" ".join(speech))
    return "\n".join(paragraphs)


def read_inputs(case_id: int) -> Optional[dict]:
    """
    The model input for the case's current information, in the evaluation harness's fields.
    Read in one transaction so it matches info_version exactly.
    """
    with get_db() as conn:
        conn.execute("BEGIN")
        case = conn.execute(
            "SELECT id, patient_info, info_version, started_at, eta_at, source_case_id FROM cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if case is None:
            return None
        version = case["info_version"]
        baseline = conn.execute(
            "SELECT MAX(info_version) FROM case_acknowledgments WHERE case_id = ? AND info_version < ?",
            (case_id, version),
        ).fetchone()[0]
        segments = conn.execute(
            """SELECT seq, text, info_version, recorded_at FROM transcript_segments
               WHERE case_id = ? AND status = 'completed' AND text != '' ORDER BY seq""",
            (case_id,),
        ).fetchall()
        updates = conn.execute(
            """SELECT id, info_version, kind, body, created_at FROM case_updates
               WHERE case_id = ? AND kind != 'eta' ORDER BY id""",
            (case_id,),
        ).fetchall()
        vitals = conn.execute(
            "SELECT update_id, name, value FROM vital_readings WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()

    vitals_by_update = {}
    for reading in vitals:
        vitals_by_update.setdefault(reading["update_id"], []).append(dict(reading))

    # (version, time, sort key, text, typed) for every piece of information. Ordered by when it was
    # spoken or typed: segments finish transcribing out of order, so their versions are not speech order.
    # The summary typed when the case was opened always comes first.
    # A version of None (text transcribed before versions were recorded) is treated as not yet seen.
    items = []
    if case["patient_info"]:
        items.append((1, case["started_at"], ("", -1), f"Typed by the crew: {case['patient_info']}", True))
    for update in updates:
        label = {"note": "Typed by the crew", "vitals": "Vitals typed by the crew",
                 "correction": "Correction typed by the crew (replaces earlier information)"}[update["kind"]]
        parts = []
        if update["id"] in vitals_by_update:
            parts.append(format_vitals(vitals_by_update[update["id"]]))
        if update["body"]:
            parts.append(update["body"])
        items.append((update["info_version"], update["created_at"], (update["created_at"], update["id"]),
                      f"{label}: {' — '.join(parts)}", True))
    for segment in segments:
        items.append((segment["info_version"], segment["recorded_at"], (segment["recorded_at"], segment["seq"]),
                      segment["text"], False))
    items.sort(key=lambda item: item[2])

    def acknowledged(item):
        return baseline is not None and item[0] is not None and item[0] <= baseline

    prior = [item for item in items if acknowledged(item)]
    current = [item for item in items if not acknowledged(item)]
    if not current:  # nothing newer than what was acknowledged: assess it all as current
        prior, current = [], items

    now = datetime.now(timezone.utc)
    elapsed = 0
    if prior and current and prior[-1][1] and current[-1][1]:
        elapsed = max(0, round((_parse_time(current[-1][1]) - _parse_time(prior[-1][1])).total_seconds() / 60))
    eta = max(0, round((_parse_time(case["eta_at"]) - now).total_seconds() / 60)) if case["eta_at"] else None
    label = case["source_case_id"] or f"case-{case_id}"
    return {
        "case_id": case_id,
        "version": version,
        "baseline_version": baseline,
        "has_information": bool(items),
        "input": {
            "case_id": label,
            "encounter_id": label,
            "update_number": 2 if prior else 1,
            "elapsed_minutes": elapsed,
            "eta_minutes": eta,
            "prior_information": _format(prior) or NO_EARLIER_REPORT,
            "current_transcript": _format(current),
        },
    }


async def score(inputs: dict):
    """
    Call the model with the evaluation prompt and schema. Returns (parsed output or None if
    unreadable, usage). Raises on any failure; never fills in defaults.
    """
    client = recordings.openai_client
    if not client:
        raise ScoringUnavailable()
    started = time.monotonic()
    response = await asyncio.get_running_loop().run_in_executor(
        assessment_executor,
        lambda: client.chat.completions.create(
            model=recordings.SCORING_MODEL,
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(inputs["input"], ensure_ascii=False)},
            ],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "ems_output", "strict": True, "schema": OUTPUT_SCHEMA}},
            extra_body=recordings.scoring_extra_body(),
            timeout=recordings.SCORING_REQUEST_TIMEOUT_SECONDS,
        ),
    )
    usage = {
        "input_tokens": getattr(response.usage, "prompt_tokens", None),
        "output_tokens": getattr(response.usage, "completion_tokens", None),
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise ScoringRefused()
    try:
        return json.loads(message.content or ""), usage
    except ValueError:
        return None, usage


def output_errors(output) -> list:
    """Same checks as evals/contracts.output_errors."""
    if not isinstance(output, dict):
        return ["output must be an object"]
    errors = []
    if set(output) != set(OUTPUT_FIELDS):
        errors.append("output fields do not match the contract")
    for key in ("summary", "change_explanation", "missing_information"):
        if not isinstance(output.get(key), str) or not output[key].strip():
            errors.append(f"missing/invalid {key}")
    if output.get("meaningful_change") not in CHANGE:
        errors.append("invalid meaningful_change")
    if output.get("preparation_category") not in PREPARATION:
        errors.append("invalid preparation_category")
    return errors


def interpret(output) -> dict:
    """
    Turn model output into the stored fields. An unreadable answer is a processing failure
    (retryable, and the last usable assessment stays current), not a clinical Needs Review.
    """
    if output_errors(output):
        return {"status": "failed", "error": UNREADABLE_ERROR}
    fields = {field: output[field].strip() for field in OUTPUT_FIELDS}
    if fields["preparation_category"] == "Unsure":
        fields["preparation_category"] = CANNOT_ASSESS
        return {**fields, "status": "needs_review", "review_reason": CANNOT_ASSESS_REASON}
    return {**fields, "status": "completed", "review_reason": None}


def estimate_cost(model: str, usage: dict) -> Optional[float]:
    prices = PRICES_PER_MILLION.get(model)
    if not prices or usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return None
    return (usage["input_tokens"] * prices[0] + usage["output_tokens"] * prices[1]) / 1_000_000


def describe_error(error: BaseException) -> str:
    """Short reason shown to clinicians after "Assessment failed: "."""
    if isinstance(error, ScoringUnavailable):
        return "AI scoring is not configured"
    if isinstance(error, ScoringRefused):
        return "the AI declined to assess this case"
    if isinstance(error, (asyncio.TimeoutError, openai.APITimeoutError)):
        return "timed out"
    if isinstance(error, openai.APIConnectionError):
        return "could not reach the AI service"
    if isinstance(error, openai.RateLimitError):
        return "AI service is busy (rate limited)"
    if isinstance(error, openai.APIError):
        return "AI service error"
    return "unexpected error"


def get_assessment(assessment_id: int) -> Optional[dict]:
    rows = query(f"SELECT {ASSESSMENT_COLUMNS} FROM risk_assessments WHERE id = ?", (assessment_id,))
    return rows[0] if rows else None


def latest_attempt(case_id: int) -> Optional[dict]:
    rows = query(
        f"SELECT {ASSESSMENT_COLUMNS} FROM risk_assessments WHERE case_id = ? ORDER BY id DESC LIMIT 1",
        (case_id,),
    )
    return rows[0] if rows else None


class AssessmentRunner:
    """At most one assessment per case at a time; information that arrives meanwhile is assessed next."""

    def __init__(self):
        self._active = set()
        self._queued = {}  # case_id -> force
        self._tasks = set()  # started by start(); referenced so they aren't garbage-collected
        # Called with the case id after every stored change; routes/cases.py publishes the case.
        self.on_change: Callable[[int], None] = lambda case_id: None

    async def request(self, case_id: int, force: bool = False) -> None:
        """
        Assess the case's current information unless an attempt already covers it.
        force re-runs even if the latest attempt (a failure) covers the current version.
        """
        if case_id in self._active:
            self._queued[case_id] = self._queued.get(case_id, False) or force
            return
        self._active.add(case_id)
        try:
            await asyncio.sleep(SETTLE_SECONDS)
            # Anything that arrived while settling is read now; keep a retry's force flag.
            force = self._queued.pop(case_id, False) or force
            await self._assess(case_id, force)
            while case_id in self._queued:
                force = self._queued.pop(case_id) or force
                await asyncio.sleep(SETTLE_SECONDS)
                await self._assess(case_id, force)
        finally:
            self._active.discard(case_id)
            self._queued.pop(case_id, None)

    def start(self, case_id: int, force: bool = False) -> None:
        """request() as a background task, for callers that aren't awaiting it (startup)."""
        task = asyncio.get_running_loop().create_task(self.request(case_id, force))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _assess(self, case_id: int, force: bool) -> None:
        inputs = read_inputs(case_id)
        if inputs is None or not inputs["has_information"]:
            return
        latest = latest_attempt(case_id)
        # An attempt covers the case if it read this information against the same acknowledged
        # baseline; an acknowledgment of an intermediate version changes the baseline.
        if latest and latest["based_on_version"] >= inputs["version"] \
                and latest["baseline_version"] == inputs["baseline_version"]:
            if not (force and latest["status"] == "failed"):
                return

        now = utc_now_iso()
        assessment_id = run(
            """INSERT INTO risk_assessments
                   (case_id, based_on_version, baseline_version, status, scorer_version, started_at, updated_at)
               VALUES (?, ?, ?, 'processing', ?, ?, ?)""",
            (case_id, inputs["version"], inputs["baseline_version"], scorer_version(), now, now),
        )["id"]
        self.on_change(case_id)

        usage = {}
        try:
            output, usage = await asyncio.wait_for(score(inputs), timeout=SCORING_TIMEOUT_SECONDS)
            fields = interpret(output)
            error = fields.pop("error", None)
        except Exception as exc:
            print(f"❌ Assessment failed for case {case_id}: {exc!r}")
            fields, error = {"status": "failed"}, describe_error(exc)

        now = utc_now_iso()
        run(
            """UPDATE risk_assessments
               SET status = ?, preparation_category = ?, meaningful_change = ?, summary = ?,
                   change_explanation = ?, missing_information = ?, review_reason = ?, error = ?,
                   input_tokens = ?, output_tokens = ?, latency_ms = ?, cost_usd = ?,
                   completed_at = ?, updated_at = ?
               WHERE id = ?""",
            (fields["status"], fields.get("preparation_category"), fields.get("meaningful_change"),
             fields.get("summary"), fields.get("change_explanation"), fields.get("missing_information"),
             fields.get("review_reason"), error, usage.get("input_tokens"), usage.get("output_tokens"),
             usage.get("latency_ms"), estimate_cost(recordings.SCORING_MODEL, usage), now, now, assessment_id),
        )
        self.on_change(case_id)


runner = AssessmentRunner()


def fail_interrupted_assessments() -> None:
    """Called at startup: assessments in flight when the server stopped will never finish on their own."""
    now = utc_now_iso()
    result = run(
        """UPDATE risk_assessments SET status = 'failed', error = 'interrupted by a server restart',
               completed_at = ?, updated_at = ?
           WHERE status = 'processing'""",
        (now, now),
    )
    if result["changes"]:
        print(f"⚠️  Marked {result['changes']} interrupted risk assessment(s) as failed")


def unassessed_open_cases() -> list:
    """
    Open cases whose current information has no usable assessment: never assessed, queued when
    the server stopped, interrupted, or failed. Nothing else would re-trigger them after a restart.
    """
    rows = query(
        """SELECT c.id FROM cases c
           WHERE c.status = 'active' AND c.info_version > COALESCE(
               (SELECT MAX(a.based_on_version) FROM risk_assessments a
                WHERE a.case_id = c.id AND a.status IN ('completed', 'needs_review')), 0)
           ORDER BY c.id"""
    )
    return [row["id"] for row in rows]
