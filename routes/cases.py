from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import asyncio
import concurrent.futures
import json
import sqlite3
import math
import uuid
import openai
import case_assessment
from case_assessment import ASSESSMENT_COLUMNS, VITAL_SIGNS
from database import get_db, query, run, to_iso, utc_now_iso
from middleware.auth import require_role, APIError
from realtime import broker, format_sse
from routes import recordings

router = APIRouter()
hospitals_router = APIRouter()

# Outside the public /uploads static mount: segment audio is PHI and is only read by the server.
SEGMENT_DIR = Path("private_uploads") / "live_segments"
MAX_SEGMENT_BYTES = 10 * 1024 * 1024
MAX_SEQ = 1_000_000
MAX_DURATION_MS = 10 * 60 * 1000
ALLOWED_SEGMENT_EXTENSIONS = {".webm", ".ogg", ".mp4", ".m4a", ".wav", ".mp3", ".aac"}

TRANSCRIPTION_TIMEOUT_SECONDS = 45
# Transient failures (timeouts, network, rate limits, 5xx) are retried before giving up.
MAX_TRANSCRIPTION_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0
# Comment lines keep idle streams open through proxies and let clients detect dead connections.
HEARTBEAT_SECONDS = 15

# Live segments get their own pool so they never queue behind slow GPT-4 analysis calls.
transcription_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="transcribe")

MAX_UPDATE_LENGTH = 2000
MAX_EMS_UNIT_LENGTH = 40
MAX_SOURCE_ID_LENGTH = 64
MAX_CLIENT_ID_LENGTH = 64
MAX_ETA_MINUTES = 24 * 60

CASE_SELECT = """
    SELECT c.*, u.first_name AS emt_first_name, u.last_name AS emt_last_name,
           h.name AS destination_hospital_name,
           (SELECT COUNT(*) FROM transcript_segments s WHERE s.case_id = c.id) AS segment_count,
           (SELECT MAX(s.recorded_at) FROM transcript_segments s WHERE s.case_id = c.id) AS last_segment_at,
           (SELECT COUNT(*) FROM transcript_segments s WHERE s.case_id = c.id AND s.status = 'pending') AS pending_segment_count,
           (SELECT COUNT(*) FROM transcript_segments s
            WHERE s.case_id = c.id AND s.status = 'failed' AND s.dismissed_at IS NULL) AS failed_segment_count,
           -- What the AI compares against: the latest acknowledgment of earlier information.
           (SELECT MAX(a.info_version) FROM case_acknowledgments a
            WHERE a.case_id = c.id AND a.info_version < c.info_version) AS baseline_version
    FROM cases c
    JOIN users u ON c.emt_id = u.id
    LEFT JOIN hospitals h ON h.id = c.destination_hospital_id
"""

SEGMENT_COLUMNS = (
    "id, case_id, seq, client_id, recorded_at, duration_ms, status, text, error, attempts, "
    "created_at, transcribed_at, updated_at, info_version, dismissed_at, dismissed_by, "
    "(SELECT first_name || ' ' || last_name FROM users WHERE users.id = dismissed_by) AS dismissed_by_name"
)

UPDATE_SELECT = """
    SELECT cu.id, cu.case_id, cu.info_version, cu.kind, cu.body, cu.eta_at, cu.author_id, cu.client_id, cu.created_at,
           u.first_name AS author_first_name, u.last_name AS author_last_name, u.role AS author_role
    FROM case_updates cu JOIN users u ON cu.author_id = u.id
"""


class TranscriptionUnavailable(Exception):
    pass


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (naive means UTC), or None if missing or invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed.astimezone(timezone.utc)  # raises OverflowError for dates near datetime.min/max
        return parsed
    except (ValueError, OverflowError):
        return None


def server_recorded_at(recorded_at: Optional[str], sent_at: Optional[str]) -> str:
    """
    Convert the client's recording start time to the server clock so it lines up with
    the case's started_at. sent_at is the client clock when the upload was sent; the
    difference from the server's receive time is the client's clock offset.
    """
    now = datetime.now(timezone.utc)
    recorded = parse_timestamp(recorded_at)
    if recorded is None:
        return to_iso(now)
    sent = parse_timestamp(sent_at)
    try:
        if sent is not None:
            recorded += now - sent
        return to_iso(min(recorded, now))
    except OverflowError:
        return to_iso(now)


def parse_id(value: str, not_found: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise APIError(404, not_found)


async def read_json_body(request: Request) -> dict:
    body_bytes = await request.body()
    if not body_bytes:
        return {}
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise APIError(400, "Invalid JSON")
    if not isinstance(body, dict):
        raise APIError(400, "Invalid JSON")
    return body


def operational_status(case: dict) -> str:
    """Where the transport is: inbound -> acknowledged (hospital saw the latest info) -> arrived -> closed."""
    if case["status"] == "closed":
        return "closed"
    if case["arrived_at"]:
        return "arrived"
    if case["latest_update_acknowledged"]:
        return "acknowledged"
    return "inbound"


def processing_summary(case: dict, latest: Optional[dict]) -> dict:
    """
    What the AI pipeline is doing for this case, kept separate from operational_status.
    status: pending | processing | completed | failed | needs_review.
    needs_review is true whenever a person should look, with the reasons listed.
    """
    pending, failed = case["pending_segment_count"], case["failed_segment_count"]
    if failed:
        transcription = "failed"
    elif pending:
        transcription = "processing"
    else:
        transcription = "completed" if case["segment_count"] else "none"

    # Information no attempt has covered yet, or an acknowledgment that changed what the latest
    # attempt compared against, is waiting to be assessed.
    if latest is None or (latest["status"] != "processing" and (
            latest["based_on_version"] < case["info_version"]
            or latest["baseline_version"] != case["baseline_version"])):
        assessment = "pending"
    else:
        assessment = latest["status"]

    reasons = []
    if failed:
        reasons.append(f"{failed} transcript segment(s) failed to transcribe")
    if assessment == "failed":
        reasons.append(f"Assessment failed: {latest['error'] or 'unknown error'}")
    if assessment == "needs_review":
        reasons.append(latest["review_reason"])

    if transcription == "failed" or assessment == "failed":
        status = "failed"
    elif assessment == "needs_review":
        status = "needs_review"
    elif transcription == "processing" or assessment == "processing":
        status = "processing"
    elif assessment == "pending":
        status = "pending"
    else:
        status = "completed"

    return {
        "status": status,
        "needs_review": bool(reasons),
        "reasons": reasons,
        "transcription": {"status": transcription, "pending": pending, "failed": failed},
        "assessment": {
            "status": assessment,
            "error": latest["error"] if latest and assessment == "failed" else None,
            "can_retry": assessment == "failed",
        },
    }


def _newest_per_case(sql: str, case_ids: list) -> dict:
    if not case_ids:
        return {}
    placeholders = ",".join("?" * len(case_ids))
    return {row["case_id"]: row for row in query(sql.format(ids=placeholders), tuple(case_ids))}


def describe_cases(rows: list) -> list:
    """Add acknowledgment, current assessment, and processing state to case rows."""
    ids = [row["id"] for row in rows]
    current = _newest_per_case(
        f"""SELECT {ASSESSMENT_COLUMNS} FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY based_on_version DESC, id DESC) AS rn
                FROM risk_assessments WHERE status IN ('completed', 'needs_review') AND case_id IN ({{ids}})
            ) WHERE rn = 1""",
        ids,
    )
    latest = _newest_per_case(
        f"""SELECT {ASSESSMENT_COLUMNS} FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY id DESC) AS rn
                FROM risk_assessments WHERE case_id IN ({{ids}})
            ) WHERE rn = 1""",
        ids,
    )
    acks = _newest_per_case(
        """SELECT case_id, info_version, acknowledged_at, user_id, first_name, last_name FROM (
               SELECT a.*, u.first_name, u.last_name,
                      ROW_NUMBER() OVER (PARTITION BY a.case_id ORDER BY a.info_version DESC, a.id DESC) AS rn
               FROM case_acknowledgments a JOIN users u ON u.id = a.user_id WHERE a.case_id IN ({ids})
           ) WHERE rn = 1""",
        ids,
    )

    described = []
    for row in rows:
        case = dict(row)
        ack = acks.get(case["id"])
        case["acknowledgment"] = ack and {
            "info_version": ack["info_version"],
            "acknowledged_at": ack["acknowledged_at"],
            "user_id": ack["user_id"],
            "user_name": f"{ack['first_name']} {ack['last_name']}",
        }
        case["latest_update_acknowledged"] = bool(ack) and ack["info_version"] >= case["info_version"]
        case["operational_status"] = operational_status(case)

        assessment = current.get(case["id"])
        case["current_assessment"] = assessment
        # None until an assessment says otherwise; "Cannot assess" when it couldn't decide.
        case["preparation_category"] = assessment["preparation_category"] if assessment else None
        case["assessment_is_outdated"] = bool(assessment) and assessment["based_on_version"] < case["info_version"]
        case["processing"] = processing_summary(case, latest.get(case["id"]))
        described.append(case)
    return described


def get_case(case_id: int) -> Optional[dict]:
    rows = query(CASE_SELECT + " WHERE c.id = ?", (case_id,))
    return describe_cases(rows)[0] if rows else None


def touch_case(case_id: int) -> Optional[dict]:
    """Bump updated_at so clients treat the re-published case as newer, and return it."""
    run("UPDATE cases SET updated_at = ? WHERE id = ?", (utc_now_iso(), case_id))
    return get_case(case_id)


def publish_case_changed(case_id: int) -> None:
    case = touch_case(case_id)
    if case:
        publish_case("case.updated", case)


case_assessment.runner.on_change = publish_case_changed


def user_hospital_id(user: dict) -> Optional[int]:
    """The hospital a doctor works at (users.hospital_id); None for EMTs and unassigned doctors."""
    rows = query("SELECT hospital_id FROM users WHERE id = ?", (user["id"],))
    return rows[0]["hospital_id"] if rows else None


def can_see_case(user: dict, case: dict) -> bool:
    """EMTs see their own cases; hospital users see cases routed to their hospital."""
    if user["role"] == "emt":
        return case["emt_id"] == user["id"]
    if user["role"] == "doctor":
        hospital_id = user_hospital_id(user)
        return hospital_id is not None and case["destination_hospital_id"] == hospital_id
    return False


def get_case_for_user(case_id_param: str, user: dict) -> dict:
    """A case the user may see. Anyone else's looks like it doesn't exist, so ids can't be probed."""
    case = get_case(parse_id(case_id_param, "Case not found"))
    if not case or not can_see_case(user, case):
        raise APIError(404, "Case not found")
    return case


def get_segment(segment_id: int) -> Optional[dict]:
    rows = query(f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE id = ?", (segment_id,))
    return rows[0] if rows else None


def get_segment_by_seq(case_id: int, seq: int) -> Optional[dict]:
    rows = query(f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE case_id = ? AND seq = ?", (case_id, seq))
    return rows[0] if rows else None


def publish_case(event_type: str, case: dict) -> None:
    broker.publish(event_type, {"case": case}, owner_id=case["emt_id"], hospital_id=case["destination_hospital_id"])


def publish_segment(event_type: str, segment: dict, case: dict) -> None:
    broker.publish(event_type, {"case_id": segment["case_id"], "segment": segment},
                   owner_id=case["emt_id"], hospital_id=case["destination_hospital_id"])


def event_filter(user: dict):
    """accepts(owner_id, hospital_id) for the live stream: the same visibility as can_see_case."""
    if user["role"] == "doctor":
        hospital_id = user_hospital_id(user)
        return lambda owner_id, case_hospital_id: hospital_id is not None and case_hospital_id == hospital_id
    user_id = user["id"]
    return lambda owner_id, case_hospital_id: owner_id == user_id


@router.get("/events")
async def case_events(current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """
    Server-Sent Events stream of case and transcript changes, for the cases the user can see:
    hospital users get cases routed to their hospital, EMTs their own.
    Every (re)connect starts with a `ready` event, after which clients should
    re-fetch state, since events sent while disconnected are not replayed.
    """
    accepts = event_filter(current_user)

    async def stream():
        sub = broker.subscribe(accepts)
        try:
            yield "retry: 3000\n\n"
            yield format_sse("ready", {"server_time": utc_now_iso()})
            while True:
                try:
                    message = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if message is None:
                    return
                yield message
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def parse_eta_minutes(value) -> str:
    """Minutes from now -> eta_at timestamp."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 <= value <= MAX_ETA_MINUTES:
        raise APIError(400, f"eta_minutes must be a number between 0 and {MAX_ETA_MINUTES}")
    return to_iso(datetime.now(timezone.utc) + timedelta(minutes=value))


def optional_text(body: dict, key: str, max_length: int) -> Optional[str]:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise APIError(400, f"{key} must be a string")
    value = value.strip()
    if len(value) > max_length:
        raise APIError(400, f"{key} must be at most {max_length} characters")
    return value or None


@router.post("", status_code=201)
async def start_case(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_role(["emt"])),
):
    """
    EMT opens a case for one patient transport. 409 if the EMT already has one open.
    Body (all optional): {"patient_info": "...", "destination_hospital_id": 1, "ems_unit": "Medic 12",
    "eta_minutes": 15}. demo/replay.py also sends source_case_id and source_run_id.
    The case starts at info_version 1: what the EMT entered here.
    """
    body = await read_json_body(request)
    patient_info = body.get("patient_info")
    if patient_info is not None and not isinstance(patient_info, str):
        raise APIError(400, "patient_info must be a string")
    ems_unit = optional_text(body, "ems_unit", MAX_EMS_UNIT_LENGTH)
    eta_at = parse_eta_minutes(body["eta_minutes"]) if body.get("eta_minutes") is not None else None
    source_case_id = optional_text(body, "source_case_id", MAX_SOURCE_ID_LENGTH)
    source_run_id = optional_text(body, "source_run_id", MAX_SOURCE_ID_LENGTH)
    hospital_id = body.get("destination_hospital_id")
    if hospital_id is not None:
        if isinstance(hospital_id, bool) or not isinstance(hospital_id, int) \
                or not query("SELECT 1 FROM hospitals WHERE id = ?", (hospital_id,)):
            raise APIError(400, "Unknown destination hospital")

    now = utc_now_iso()
    try:
        result = run(
            """INSERT INTO cases (emt_id, patient_info, status, started_at, updated_at,
                                  destination_hospital_id, ems_unit, eta_at, info_version, last_update_at,
                                  source_case_id, source_run_id)
               VALUES (?, ?, 'active', ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (current_user["id"], patient_info, now, now, hospital_id, ems_unit, eta_at, now,
             source_case_id, source_run_id),
        )
    except sqlite3.IntegrityError:
        # idx_cases_one_active_per_emt: an EMT has at most one open case at a time.
        raise APIError(409, "You already have an active case")
    case = get_case(result["id"])
    publish_case("case.opened", case)
    background_tasks.add_task(case_assessment.runner.request, case["id"])
    return case


@router.get("")
async def list_cases(
    status: Optional[str] = None,
    hospital_id: Optional[str] = None,
    current_user: dict = Depends(require_role(["emt", "doctor"])),
):
    """Active cases first, then most recent. Hospital users see cases routed to their hospital; EMTs their own."""
    conditions, params = [], []
    if current_user["role"] == "emt":
        conditions.append("c.emt_id = ?")
        params.append(current_user["id"])
    else:
        own_hospital = user_hospital_id(current_user)
        if own_hospital is None:
            return []
        conditions.append("c.destination_hospital_id = ?")
        params.append(own_hospital)
    if status in ("active", "closed"):
        conditions.append("c.status = ?")
        params.append(status)
    if hospital_id is not None:
        conditions.append("c.destination_hospital_id = ?")
        params.append(parse_id(hospital_id, "Hospital not found"))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return describe_cases(query(
        CASE_SELECT + where + " ORDER BY (c.status = 'active') DESC, c.started_at DESC LIMIT 50",
        tuple(params),
    ))


@router.get("/{case_id}")
async def get_case_details(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    return get_case_for_user(case_id, current_user)


@router.post("/{case_id}/close")
async def close_case(case_id: str, current_user: dict = Depends(require_role(["emt"]))):
    case = get_case_for_user(case_id, current_user)
    if case["status"] == "active":
        now = utc_now_iso()
        run("UPDATE cases SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?", (now, now, case["id"]))
        case = get_case(case["id"])
        publish_case("case.updated", case)
    return case


@router.post("/{case_id}/arrive")
async def mark_arrived(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """The EMT or the hospital marks the patient as handed over. Idempotent."""
    case = get_case_for_user(case_id, current_user)
    if not case["arrived_at"]:
        now = utc_now_iso()
        run("UPDATE cases SET arrived_at = ?, updated_at = ? WHERE id = ? AND arrived_at IS NULL", (now, now, case["id"]))
        case = get_case(case["id"])
        publish_case("case.updated", case)
    return case


@router.post("/{case_id}/acknowledge")
async def acknowledge_case(
    case_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_role(["doctor"])),
):
    """
    A hospital user confirms they have seen the case. Body (optional): {"info_version": n}, the
    version their screen showed, so information that arrived after they looked stays unacknowledged.
    Defaults to the current version.
    """
    case = get_case_for_user(case_id, current_user)
    body = await read_json_body(request)
    version = body.get("info_version", case["info_version"])
    if isinstance(version, bool) or not isinstance(version, int) or not 0 < version <= case["info_version"]:
        raise APIError(400, f"info_version must be between 1 and {case['info_version']}")

    ack = case["acknowledgment"]
    if ack and ack["info_version"] >= version:
        return case
    run(
        "INSERT INTO case_acknowledgments (case_id, user_id, info_version, acknowledged_at) VALUES (?, ?, ?, ?)",
        (case["id"], current_user["id"], version, utc_now_iso()),
    )
    case = touch_case(case["id"])
    publish_case("case.updated", case)
    if version < case["info_version"]:
        # The AI compares newer information against this acknowledgment now; re-assess.
        background_tasks.add_task(case_assessment.runner.request, case["id"])
    return case


def parse_vitals(value) -> dict:
    """{"spo2": 91, "hr": 110} -> validated readings. Empty fields (null) are skipped."""
    if not isinstance(value, dict):
        raise APIError(400, "vitals must be an object of readings")
    readings = {}
    for name, number in value.items():
        if number is None:
            continue
        if name not in VITAL_SIGNS:
            raise APIError(400, f"Unknown vital sign: {name}")
        _, _, low, high = VITAL_SIGNS[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) \
                or not low <= number <= high:
            raise APIError(400, f"{name} must be a number between {low} and {high}")
        readings[name] = float(number)
    if not readings:
        raise APIError(400, "Enter at least one vital sign")
    return readings


def describe_reading(reading: dict) -> dict:
    label, unit, _, _ = VITAL_SIGNS.get(reading["name"], (reading["name"], "", 0, 0))
    return {**reading, "label": label, "unit": unit}


def get_updates(case_id: int, update_id: Optional[int] = None) -> list:
    """A case's typed updates, oldest first, each with the vital sign readings it recorded."""
    where, params = "WHERE cu.case_id = ?", [case_id]
    if update_id is not None:
        where += " AND cu.id = ?"
        params.append(update_id)
    updates = query(UPDATE_SELECT + where + " ORDER BY cu.id", tuple(params))
    readings = query(
        "SELECT id, update_id, name, value, measured_at FROM vital_readings WHERE case_id = ? ORDER BY id",
        (case_id,),
    )
    for update in updates:
        update["vitals"] = [describe_reading(r) for r in readings if r["update_id"] == update["id"]]
    return updates


@router.post("/{case_id}/updates", status_code=201)
async def add_update(
    case_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_role(["emt"])),
):
    """
    EMT adds information to an open case. Body:
      {"kind": "note" | "correction", "body": "..."}
      {"kind": "vitals", "vitals": {"spo2": 91, "hr": 110}, "body": "optional comment"}
      {"kind": "eta", "eta_minutes": 8}
    plus an optional "client_id"; a retry with the same client_id returns the stored update with 200.
    Patient information (everything but eta) raises the case's info_version and is re-assessed.
    """
    case = get_case_for_user(case_id, current_user)
    body = await read_json_body(request)
    kind = body.get("kind")
    if kind not in ("note", "vitals", "correction", "eta"):
        raise APIError(400, "kind must be one of note, vitals, correction, eta")
    text = optional_text(body, "body", MAX_UPDATE_LENGTH)
    if kind in ("note", "correction") and not text:
        raise APIError(400, "body is required")
    vitals = parse_vitals(body.get("vitals")) if kind == "vitals" else {}
    eta_at = parse_eta_minutes(body.get("eta_minutes")) if kind == "eta" else None
    if kind == "eta":
        text = None
    client_id = body.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or not 0 < len(client_id) <= MAX_CLIENT_ID_LENGTH):
        raise APIError(400, "Invalid client_id")

    with get_db() as conn:
        # Write lock first, so a retry can't race the original past the client_id check.
        conn.execute("BEGIN IMMEDIATE")
        if client_id is not None:
            existing = conn.execute(
                "SELECT id FROM case_updates WHERE case_id = ? AND client_id = ?", (case["id"], client_id)
            ).fetchone()
            if existing:
                return JSONResponse(status_code=200, content=get_updates(case["id"], existing["id"])[0])
        current = conn.execute("SELECT status, info_version FROM cases WHERE id = ?", (case["id"],)).fetchone()
        if current["status"] != "active":
            raise APIError(409, "Case is closed")

        now = utc_now_iso()
        if kind == "eta":
            # Logistics, not patient information: no new version, no re-assessment.
            version = current["info_version"]
            conn.execute("UPDATE cases SET eta_at = ?, updated_at = ? WHERE id = ?", (eta_at, now, case["id"]))
        else:
            version = current["info_version"] + 1
            conn.execute(
                "UPDATE cases SET info_version = ?, last_update_at = ?, updated_at = ? WHERE id = ?",
                (version, now, now, case["id"]),
            )
        update_id = conn.execute(
            """INSERT INTO case_updates (case_id, info_version, kind, body, eta_at, author_id, client_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (case["id"], version, kind, text, eta_at, current_user["id"], client_id, now),
        ).lastrowid
        conn.executemany(
            "INSERT INTO vital_readings (case_id, update_id, name, value, measured_at) VALUES (?, ?, ?, ?, ?)",
            [(case["id"], update_id, name, value, now) for name, value in vitals.items()],
        )

    update = get_updates(case["id"], update_id)[0]
    broker.publish("update.created", {"case_id": case["id"], "update": update},
                   owner_id=case["emt_id"], hospital_id=case["destination_hospital_id"])
    publish_case("case.updated", get_case(case["id"]))
    if kind != "eta":
        background_tasks.add_task(case_assessment.runner.request, case["id"])
    return update


@router.get("/{case_id}/updates")
async def list_updates(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    case = get_case_for_user(case_id, current_user)
    return get_updates(case["id"])


@router.get("/{case_id}/vitals")
async def list_vitals(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """Every vital sign reading, oldest first, so trends (SpO2 96 -> 91 -> 86) can be shown."""
    case = get_case_for_user(case_id, current_user)
    readings = query(
        "SELECT id, update_id, name, value, measured_at FROM vital_readings WHERE case_id = ? ORDER BY id",
        (case["id"],),
    )
    return [describe_reading(r) for r in readings]


@router.get("/{case_id}/assessments")
async def list_assessments(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """Every assessment attempt, newest first, including failed ones."""
    case = get_case_for_user(case_id, current_user)
    return query(
        f"SELECT {ASSESSMENT_COLUMNS} FROM risk_assessments WHERE case_id = ? ORDER BY id DESC",
        (case["id"],),
    )


@router.post("/{case_id}/assessments/retry", status_code=202)
async def retry_assessment(
    case_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_role(["emt", "doctor"])),
):
    """Re-run a failed risk assessment on the case's current information."""
    case = get_case_for_user(case_id, current_user)
    if not case["processing"]["assessment"]["can_retry"]:
        raise APIError(409, "Only a failed assessment can be retried")
    background_tasks.add_task(case_assessment.runner.request, case["id"], True)
    return case


@hospitals_router.get("")
async def list_hospitals():
    """Public, like /api/doctors/available: the sign-up form needs it, and names aren't sensitive."""
    return query("SELECT id, code, name FROM hospitals ORDER BY name")


@hospitals_router.get("/mine")
async def my_hospital(current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """The hospital whose cases this user sees; null for EMTs and unassigned hospital users."""
    hospital_id = user_hospital_id(current_user)
    rows = query("SELECT id, code, name FROM hospitals WHERE id = ?", (hospital_id,)) if hospital_id else []
    return rows[0] if rows else None


@router.get("/{case_id}/segments")
async def list_segments(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """Full transcript history for a case in chronological (seq) order."""
    case = get_case_for_user(case_id, current_user)
    return query(
        f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE case_id = ? ORDER BY seq ASC",
        (case["id"],),
    )


def parse_bounded_int(value: Optional[str], name: str, maximum: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise APIError(400, f"{name} must be a number")
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise APIError(400, f"{name} must be between 0 and {maximum}")
    return int(number)


def existing_upload(case_id: int, seq: int, client_id: str) -> Optional[JSONResponse]:
    """200 with the stored row if this exact clip was already uploaded; 409 if another clip holds the seq."""
    existing = get_segment_by_seq(case_id, seq)
    if existing is None:
        return None
    if existing["client_id"] != client_id:
        raise APIError(409, f"Segment {seq} already exists with different audio")
    return JSONResponse(status_code=200, content=existing)


@router.post("/{case_id}/segments", status_code=201)
async def upload_segment(
    case_id: str,
    background_tasks: BackgroundTasks,
    audio: Optional[UploadFile] = File(None),
    seq: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    recorded_at: Optional[str] = Form(None),
    sent_at: Optional[str] = Form(None),
    duration_ms: Optional[str] = Form(None),
    current_user: dict = Depends(require_role(["emt"])),
):
    """
    Upload one audio segment of a live case for transcription.
    - seq: 0-based position of the segment in the case; defines transcript order.
    - client_id: unique id of this recorded clip. Re-sending the same (seq, client_id)
      returns the stored segment with 200; a different clip with a taken seq is a 409.
    - recorded_at / sent_at: client-clock times when recording started and when this
      upload was sent; used to place recorded_at on the server clock.
    """
    case = get_case_for_user(case_id, current_user)

    if seq is None or not seq.strip().isdigit():
        raise APIError(400, "seq must be a non-negative integer")
    seq_value = parse_bounded_int(seq, "seq", MAX_SEQ)
    if not client_id or len(client_id) > 64:
        raise APIError(400, "client_id is required (max 64 characters)")
    duration_value = parse_bounded_int(duration_ms, "duration_ms", MAX_DURATION_MS) if duration_ms else None

    duplicate = existing_upload(case["id"], seq_value, client_id)
    if duplicate:
        return duplicate
    if case["status"] != "active":
        raise APIError(409, "Case is closed")

    if audio is None:
        raise APIError(400, "audio file is required")
    if not recordings.is_audio_type_allowed(audio.content_type):
        raise APIError(400, "Unsupported audio type")
    content = await audio.read()
    if not content:
        raise APIError(400, "audio file is empty")
    if len(content) > MAX_SEGMENT_BYTES:
        raise APIError(413, "Audio segment too large")

    # The transcription model infers the audio format from the file extension.
    extension = Path(audio.filename or "").suffix.lower()
    if extension not in ALLOWED_SEGMENT_EXTENSIONS:
        extension = ".webm"
    SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = SEGMENT_DIR / f"case-{case['id']}-seg-{seq_value}-{uuid.uuid4().hex}{extension}"
    await asyncio.get_running_loop().run_in_executor(recordings.fast_executor, audio_path.write_bytes, content)

    now = utc_now_iso()
    try:
        # Inserting only while the case is active makes the check atomic with a concurrent close.
        result = run(
            """INSERT INTO transcript_segments
                   (case_id, seq, client_id, recorded_at, duration_ms, audio_file_path, status, created_at, updated_at)
               SELECT ?, ?, ?, ?, ?, ?, 'pending', ?, ?
               WHERE EXISTS (SELECT 1 FROM cases WHERE id = ? AND status = 'active')""",
            (case["id"], seq_value, client_id, server_recorded_at(recorded_at, sent_at), duration_value,
             str(audio_path), now, now, case["id"]),
        )
    except sqlite3.IntegrityError:
        # A concurrent upload of the same seq won the race.
        audio_path.unlink(missing_ok=True)
        return existing_upload(case["id"], seq_value, client_id)
    except Exception:
        audio_path.unlink(missing_ok=True)
        raise
    if result["changes"] == 0:
        audio_path.unlink(missing_ok=True)
        raise APIError(409, "Case is closed")

    # Bumping the case lets clients see its new segment_count / last_segment_at as a newer version.
    run("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case["id"]))
    segment = get_segment(result["id"])
    publish_segment("segment.created", segment, case)
    publish_case("case.updated", get_case(case["id"]))
    background_tasks.add_task(transcribe_segment, segment["id"])
    return segment


@router.post("/{case_id}/segments/{segment_id}/retry")
async def retry_segment(
    case_id: str,
    segment_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_role(["emt"])),
):
    """Re-run transcription for a failed segment using its stored audio."""
    case = get_case_for_user(case_id, current_user)
    segment = get_segment(parse_id(segment_id, "Segment not found"))
    if not segment or segment["case_id"] != case["id"]:
        raise APIError(404, "Segment not found")
    # Conditional update so a double-click can't start two transcriptions.
    claimed = run(
        """UPDATE transcript_segments SET status = 'pending', error = NULL, dismissed_at = NULL, dismissed_by = NULL,
               updated_at = ? WHERE id = ? AND status = 'failed'""",
        (utc_now_iso(), segment["id"]),
    )
    if claimed["changes"] == 0:
        raise APIError(409, "Only failed segments can be retried")

    segment = get_segment(segment["id"])
    publish_segment("segment.updated", segment, case)
    # The case's failed/pending counts changed.
    publish_case_changed(case["id"])
    background_tasks.add_task(transcribe_segment, segment["id"])
    return segment


@router.post("/{case_id}/segments/{segment_id}/dismiss")
async def dismiss_segment(case_id: str, segment_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """
    Mark a failed segment as handled (e.g. its content was re-sent as text), so it no longer
    flags the case for review. The failure stays on record with who dismissed it and when;
    a later retry clears the dismissal.
    """
    case = get_case_for_user(case_id, current_user)
    segment = get_segment(parse_id(segment_id, "Segment not found"))
    if not segment or segment["case_id"] != case["id"]:
        raise APIError(404, "Segment not found")
    now = utc_now_iso()
    dismissed = run(
        """UPDATE transcript_segments SET dismissed_at = ?, dismissed_by = ?, updated_at = ?
           WHERE id = ? AND status = 'failed' AND dismissed_at IS NULL""",
        (now, current_user["id"], now, segment["id"]),
    )
    if dismissed["changes"] == 0 and not segment["dismissed_at"]:
        raise APIError(409, "Only failed segments can be dismissed")

    segment = get_segment(segment["id"])
    publish_segment("segment.updated", segment, case)
    publish_case_changed(case["id"])
    return segment


def transcribe_segment_sync(audio_path: str, prompt: Optional[str]) -> str:
    client = recordings.openai_client
    if not client:
        raise TranscriptionUnavailable()
    kwargs = {"prompt": prompt} if prompt else {}
    with open(audio_path, "rb") as audio_file:
        return client.audio.transcriptions.create(
            file=audio_file,
            model=recordings.TRANSCRIPTION_MODEL,
            response_format="text",
            timeout=TRANSCRIPTION_TIMEOUT_SECONDS,
            **kwargs,
        )


def describe_transcription_error(error: Exception):
    """Return (reason shown to clinicians after "Transcription failed: ", whether retrying might help)."""
    if isinstance(error, TranscriptionUnavailable):
        return "transcription service is not configured", False
    if isinstance(error, openai.APITimeoutError):
        return "timed out", True
    if isinstance(error, openai.APIConnectionError):
        return "could not reach the transcription service", True
    if isinstance(error, openai.RateLimitError):
        return "transcription service is busy (rate limited)", True
    if isinstance(error, openai.InternalServerError):
        return "transcription service error", True
    if isinstance(error, openai.AuthenticationError):
        return "transcription service rejected the API key", False
    if isinstance(error, openai.BadRequestError):
        return "unsupported or corrupt audio", False
    return "unexpected error", False


def previous_segment_text(segment: dict) -> Optional[str]:
    """Tail of the preceding segment's transcript, passed to the transcription model for continuity across cuts."""
    rows = query(
        """SELECT text FROM transcript_segments
           WHERE case_id = ? AND seq < ? AND status = 'completed' AND text != ''
           ORDER BY seq DESC LIMIT 1""",
        (segment["case_id"], segment["seq"]),
    )
    return rows[0]["text"][-200:] if rows else None


async def transcribe_segment(segment_id: int):
    """Transcribe one segment, store the result, and publish it. Never leaves the segment pending."""
    rows = query(
        """SELECT s.id, s.case_id, s.seq, s.audio_file_path, c.emt_id, c.destination_hospital_id
           FROM transcript_segments s JOIN cases c ON s.case_id = c.id
           WHERE s.id = ?""",
        (segment_id,),
    )
    if not rows:
        return
    segment = rows[0]

    try:
        text, error_message = await run_transcription(segment)
    except Exception as error:
        print(f"❌ Unexpected error transcribing segment {segment_id}: {error!r}")
        text, error_message = None, "unexpected error"

    now = utc_now_iso()
    text = str(text).strip() if text is not None else None
    with get_db() as conn:
        if text is not None:
            conn.execute(
                """UPDATE transcript_segments
                   SET status = 'completed', text = ?, error = NULL, transcribed_at = ?, updated_at = ? WHERE id = ?""",
                (text, now, now, segment_id),
            )
        else:
            conn.execute(
                "UPDATE transcript_segments SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (error_message, now, segment_id),
            )
        # New transcript text is new patient information; in the same transaction, so the
        # version an assessment reads always matches the transcript it sees.
        if text:
            conn.execute(
                "UPDATE cases SET info_version = info_version + 1, last_update_at = ?, updated_at = ? WHERE id = ?",
                (now, now, segment["case_id"]),
            )
            conn.execute(
                "UPDATE transcript_segments SET info_version = (SELECT info_version FROM cases WHERE id = ?) WHERE id = ?",
                (segment["case_id"], segment_id),
            )
        else:
            conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, segment["case_id"]))
    publish_segment("segment.updated", get_segment(segment_id), segment)
    publish_case("case.updated", get_case(segment["case_id"]))
    if text:
        await case_assessment.runner.request(segment["case_id"])


async def run_transcription(segment: dict):
    """Call the transcription model, retrying transient failures. Returns (text, None) or (None, error message)."""
    prompt = previous_segment_text(segment)
    loop = asyncio.get_running_loop()
    for attempt in range(1, MAX_TRANSCRIPTION_ATTEMPTS + 1):
        run("UPDATE transcript_segments SET attempts = attempts + 1 WHERE id = ?", (segment["id"],))
        try:
            text = await loop.run_in_executor(
                transcription_executor, transcribe_segment_sync, segment["audio_file_path"], prompt
            )
            return text, None
        except Exception as error:
            print(f"❌ Transcription error for segment {segment['id']} (attempt {attempt}): {error!r}")
            error_message, transient = describe_transcription_error(error)
            if not transient or attempt == MAX_TRANSCRIPTION_ATTEMPTS:
                return None, error_message
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)


def fail_interrupted_segments() -> None:
    """Called at startup: transcriptions in flight when the server stopped will never finish on their own."""
    result = run(
        """UPDATE transcript_segments SET status = 'failed', error = 'interrupted by a server restart', updated_at = ?
           WHERE status = 'pending'""",
        (utc_now_iso(),),
    )
    if result["changes"]:
        print(f"⚠️  Marked {result['changes']} interrupted transcript segment(s) as failed")
