"""Risk/priority assessment for EMS cases.

Every attempt is stored in risk_assessments with the case info_version it read, so:
- a failure never overwrites earlier information or the last usable assessment;
- a slow result for older information can't replace a newer one (the current
  assessment is the usable one with the highest based_on_version);
- a missing or invalid score is stored as NULL and marked needs_review, never
  replaced by a normal-looking default.

The scorer reuses the existing GPT-4 prompt (routes/recordings.analyze_with_llm). The
risk/priority scales are the app's current ones until KAN-14 settles the definitions;
KAN-8 is expected to replace score() with the extraction -> scoring pipeline.

Runs in-process (like realtime.py), so use a single server worker.
"""
import asyncio
from typing import Callable, Optional

import openai

from database import get_db, query, run, utc_now_iso
from routes import recordings

SCORER_VERSION = "gpt-4/legacy-recording-prompt"
SCORING_TIMEOUT_SECONDS = 90
# A live case gets new transcript every ~8s. While one assessment runs, newer information is
# queued and assessed once, at least this long after the previous run started.
MIN_SECONDS_BETWEEN_RUNS = 15.0

# "Unsure" is the older wording still used by evals/ prompts; both mean Cannot assess.
CANNOT_ASSESS = ("Cannot assess", "Unsure")
CANNOT_ASSESS_REASON = "Insufficient information to assess preparation needs."
INSUFFICIENT_INFO_REASON = "Insufficient information to assess risk."
INVALID_SCORE_REASON = "The scorer did not return a valid risk score and priority."

ASSESSMENT_COLUMNS = (
    "id, case_id, based_on_version, status, risk_score, priority_level, chief_complaint, summary, "
    "critical_info, review_reason, error, scorer_version, started_at, completed_at, updated_at"
)

# Same ranges as the recordings table until KAN-14 defines the scales.
RISK_RANGE = (0, 10)
PRIORITY_RANGE = (1, 5)


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


def read_inputs(case_id: int) -> Optional[dict]:
    """Everything the scorer may use, read in one transaction so it matches info_version exactly."""
    with get_db() as conn:
        conn.execute("BEGIN")
        case = conn.execute(
            "SELECT id, patient_info, info_version FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if case is None:
            return None
        segments = conn.execute(
            """SELECT text FROM transcript_segments
               WHERE case_id = ? AND status = 'completed' AND text != '' ORDER BY seq""",
            (case_id,),
        ).fetchall()
        updates = conn.execute(
            "SELECT id, kind, body, created_at FROM case_updates WHERE case_id = ? AND kind != 'eta' ORDER BY id",
            (case_id,),
        ).fetchall()
        vitals = conn.execute(
            "SELECT update_id, name, value FROM vital_readings WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()

    vitals_by_update = {}
    for reading in vitals:
        vitals_by_update.setdefault(reading["update_id"], []).append(dict(reading))

    lines = []
    if case["patient_info"]:
        lines.append(f"Initial report: {case['patient_info']}")
    for update in updates:
        label = {"note": "Update", "vitals": "Vitals", "correction": "Correction (replaces earlier information)"}[update["kind"]]
        parts = []
        if update["id"] in vitals_by_update:
            parts.append(format_vitals(vitals_by_update[update["id"]]))
        if update["body"]:
            parts.append(update["body"])
        lines.append(f"[{update['created_at']}] {label}: {' — '.join(parts)}")

    transcript = "\n".join(row["text"] for row in segments)
    return {
        "case_id": case_id,
        "version": case["info_version"],
        "transcript": transcript,
        "patient_info": "\n".join(lines),
        "has_information": bool(transcript or lines),
    }


async def score(inputs: dict) -> dict:
    """The raw scorer output. Raises on any failure; never fills in defaults."""
    if not recordings.openai_client:
        raise ScoringUnavailable()
    return await recordings.analyze_with_llm(inputs["transcript"], inputs["patient_info"])


def _bounded_int(value, bounds) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and bounds[0] <= value <= bounds[1]:
        return value
    return None


def _text(value) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def interpret(result) -> dict:
    """Turn scorer output into the stored fields. Anything unusable becomes needs_review with a reason."""
    if not isinstance(result, dict):
        return {"status": "needs_review", "review_reason": "The scorer returned an unreadable result."}

    fields = {
        "chief_complaint": _text(result.get("chief_complaint")),
        "summary": _text(result.get("medical_summary")),
        "critical_info": _text(result.get("critical_info")),
        "risk_score": None,
        "priority_level": None,
    }
    # preparation_category comes from the clinician-annotation contract (Cannot assess -> Needs Review).
    if result.get("preparation_category") in CANNOT_ASSESS:
        return {**fields, "status": "needs_review", "review_reason": CANNOT_ASSESS_REASON}
    if result.get("insufficient_information") is True:
        return {**fields, "status": "needs_review", "review_reason": INSUFFICIENT_INFO_REASON}

    risk = _bounded_int(result.get("risk_score"), RISK_RANGE)
    priority = _bounded_int(result.get("priority_level"), PRIORITY_RANGE)
    if risk is None or priority is None:
        return {**fields, "status": "needs_review", "review_reason": INVALID_SCORE_REASON}
    return {**fields, "status": "completed", "risk_score": risk, "priority_level": priority, "review_reason": None}


def describe_error(error: BaseException) -> str:
    """Short reason shown to clinicians after "Risk assessment failed: "."""
    if isinstance(error, ScoringUnavailable):
        return "AI scoring is not configured"
    if isinstance(error, asyncio.TimeoutError):
        return "timed out"
    if isinstance(error, openai.APIConnectionError):
        return "could not reach the AI service"
    if isinstance(error, openai.RateLimitError):
        return "AI service is busy (rate limited)"
    if isinstance(error, openai.APIError):
        return "AI service error"
    if isinstance(error, ValueError):  # includes json.JSONDecodeError from an unparseable reply
        return "AI returned a response that could not be read"
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
            await self._assess(case_id, force)
            while case_id in self._queued:
                force = self._queued.pop(case_id)
                await asyncio.sleep(MIN_SECONDS_BETWEEN_RUNS)
                await self._assess(case_id, force)
        finally:
            self._active.discard(case_id)
            self._queued.pop(case_id, None)

    async def _assess(self, case_id: int, force: bool) -> None:
        inputs = read_inputs(case_id)
        if inputs is None or not inputs["has_information"]:
            return
        latest = latest_attempt(case_id)
        if latest and latest["based_on_version"] >= inputs["version"]:
            if not (force and latest["status"] == "failed"):
                return

        now = utc_now_iso()
        assessment_id = run(
            """INSERT INTO risk_assessments (case_id, based_on_version, status, scorer_version, started_at, updated_at)
               VALUES (?, ?, 'processing', ?, ?, ?)""",
            (case_id, inputs["version"], SCORER_VERSION, now, now),
        )["id"]
        self.on_change(case_id)

        try:
            result = await asyncio.wait_for(score(inputs), timeout=SCORING_TIMEOUT_SECONDS)
            fields = interpret(result)
            error = None
        except Exception as exc:
            print(f"❌ Risk assessment failed for case {case_id}: {exc!r}")
            fields, error = {"status": "failed"}, describe_error(exc)

        now = utc_now_iso()
        run(
            """UPDATE risk_assessments
               SET status = ?, risk_score = ?, priority_level = ?, chief_complaint = ?, summary = ?,
                   critical_info = ?, review_reason = ?, error = ?, completed_at = ?, updated_at = ?
               WHERE id = ?""",
            (fields["status"], fields.get("risk_score"), fields.get("priority_level"), fields.get("chief_complaint"),
             fields.get("summary"), fields.get("critical_info"), fields.get("review_reason"), error, now, now,
             assessment_id),
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
