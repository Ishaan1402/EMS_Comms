from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import asyncio
import concurrent.futures
import json
import sqlite3
import time
import openai
from database import query, run
from middleware.auth import require_role, APIError
from realtime import broker, format_sse
from routes import recordings

router = APIRouter()

SEGMENT_DIR = Path("uploads") / "segments"
MAX_SEGMENT_BYTES = 10 * 1024 * 1024
ALLOWED_SEGMENT_EXTENSIONS = {".webm", ".ogg", ".mp4", ".m4a", ".wav", ".mp3", ".aac"}

TRANSCRIPTION_TIMEOUT_SECONDS = 45
# Transient failures (timeouts, network, rate limits, 5xx) are retried before giving up.
MAX_TRANSCRIPTION_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0
# Comment lines keep idle streams open through proxies and let clients detect dead connections.
HEARTBEAT_SECONDS = 15

# Live segments get their own pool so they never queue behind slow GPT-4 analysis calls.
transcription_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="transcribe")

CASE_SELECT = """
    SELECT c.*, u.first_name AS emt_first_name, u.last_name AS emt_last_name,
           (SELECT COUNT(*) FROM transcript_segments s WHERE s.case_id = c.id) AS segment_count,
           (SELECT MAX(s.recorded_at) FROM transcript_segments s WHERE s.case_id = c.id) AS last_segment_at
    FROM cases c
    JOIN users u ON c.emt_id = u.id
"""

SEGMENT_COLUMNS = "id, case_id, seq, recorded_at, duration_ms, status, text, error, attempts, created_at, transcribed_at"


class TranscriptionUnavailable(Exception):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_timestamp(value: Optional[str]) -> Optional[str]:
    """Parse a client ISO-8601 timestamp into UTC 'YYYY-MM-DDTHH:MM:SS.sssZ', or None if invalid."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def get_case(case_id: int) -> Optional[dict]:
    rows = query(CASE_SELECT + " WHERE c.id = ?", (case_id,))
    return rows[0] if rows else None


def get_case_for_user(case_id_param: str, user: dict) -> dict:
    """Doctors can see every case; EMTs only their own. Other EMTs' cases look like they don't exist."""
    case = get_case(parse_id(case_id_param, "Case not found"))
    if not case or (user["role"] == "emt" and case["emt_id"] != user["id"]):
        raise APIError(404, "Case not found")
    return case


def get_segment(segment_id: int) -> Optional[dict]:
    rows = query(f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE id = ?", (segment_id,))
    return rows[0] if rows else None


def publish_case(event_type: str, case: dict) -> None:
    broker.publish(event_type, {"case": case}, owner_id=case["emt_id"])


def publish_segment(event_type: str, segment: dict, emt_id: int) -> None:
    broker.publish(event_type, {"case_id": segment["case_id"], "segment": segment}, owner_id=emt_id)


@router.get("/events")
async def case_events(current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """
    Server-Sent Events stream of case and transcript changes.
    Doctors receive events for every case; EMTs only for their own.
    Every (re)connect starts with a `ready` event, after which clients should
    re-fetch state, since events sent while disconnected are not replayed.
    """
    if current_user["role"] == "doctor":
        accepts = lambda owner_id: True
    else:
        user_id = current_user["id"]
        accepts = lambda owner_id: owner_id == user_id

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


@router.post("", status_code=201)
async def start_case(request: Request, current_user: dict = Depends(require_role(["emt"]))):
    """EMT opens a live case. Body (optional): {"patient_info": "..."}."""
    body = await read_json_body(request)
    patient_info = body.get("patient_info")
    if patient_info is not None and not isinstance(patient_info, str):
        raise APIError(400, "patient_info must be a string")

    result = run(
        "INSERT INTO cases (emt_id, patient_info, status, started_at) VALUES (?, ?, 'active', ?)",
        (current_user["id"], patient_info, utc_now_iso()),
    )
    case = get_case(result["id"])
    publish_case("case.opened", case)
    return case


@router.get("")
async def list_cases(status: Optional[str] = None, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """Active cases first, then most recent. Doctors see all cases; EMTs see their own."""
    conditions, params = [], []
    if current_user["role"] == "emt":
        conditions.append("c.emt_id = ?")
        params.append(current_user["id"])
    if status in ("active", "closed"):
        conditions.append("c.status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return query(
        CASE_SELECT + where + " ORDER BY (c.status = 'active') DESC, c.started_at DESC LIMIT 50",
        tuple(params),
    )


@router.get("/{case_id}")
async def get_case_details(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    return get_case_for_user(case_id, current_user)


@router.post("/{case_id}/close")
async def close_case(case_id: str, current_user: dict = Depends(require_role(["emt"]))):
    case = get_case_for_user(case_id, current_user)
    if case["status"] == "active":
        run("UPDATE cases SET status = 'closed', closed_at = ? WHERE id = ?", (utc_now_iso(), case["id"]))
        case = get_case(case["id"])
        publish_case("case.updated", case)
    return case


@router.get("/{case_id}/segments")
async def list_segments(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """Full transcript history for a case in chronological (seq) order."""
    case = get_case_for_user(case_id, current_user)
    return query(
        f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE case_id = ? ORDER BY seq ASC",
        (case["id"],),
    )


@router.post("/{case_id}/segments", status_code=201)
async def upload_segment(
    case_id: str,
    background_tasks: BackgroundTasks,
    audio: Optional[UploadFile] = File(None),
    seq: Optional[str] = Form(None),
    recorded_at: Optional[str] = Form(None),
    duration_ms: Optional[str] = Form(None),
    current_user: dict = Depends(require_role(["emt"])),
):
    """
    Upload one audio segment of a live case for transcription.
    `seq` is the segment's 0-based position in the case and makes uploads idempotent:
    re-sending a seq that already exists returns the existing segment with 200.
    `recorded_at` is when the EMT started recording this segment (ISO-8601).
    """
    case = get_case_for_user(case_id, current_user)

    try:
        seq_value = int(seq)
        if seq_value < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise APIError(400, "seq must be a non-negative integer")

    existing = query(
        f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE case_id = ? AND seq = ?",
        (case["id"], seq_value),
    )
    if existing:
        return JSONResponse(status_code=200, content=existing[0])

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

    duration_value = None
    if duration_ms:
        try:
            duration_value = max(0, int(float(duration_ms)))
        except ValueError:
            raise APIError(400, "duration_ms must be a number")

    now = utc_now_iso()
    recorded_at_value = normalize_timestamp(recorded_at) or now

    # Whisper infers the audio format from the file extension.
    extension = Path(audio.filename or "").suffix.lower()
    if extension not in ALLOWED_SEGMENT_EXTENSIONS:
        extension = ".webm"
    SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = SEGMENT_DIR / f"case-{case['id']}-seg-{seq_value}-{int(time.time() * 1000)}{extension}"
    await asyncio.get_running_loop().run_in_executor(recordings.fast_executor, audio_path.write_bytes, content)

    try:
        result = run(
            """INSERT INTO transcript_segments
                   (case_id, seq, recorded_at, duration_ms, audio_file_path, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (case["id"], seq_value, recorded_at_value, duration_value, str(audio_path), now),
        )
    except sqlite3.IntegrityError:
        # A concurrent duplicate upload won the race; keep its row.
        audio_path.unlink(missing_ok=True)
        existing = query(
            f"SELECT {SEGMENT_COLUMNS} FROM transcript_segments WHERE case_id = ? AND seq = ?",
            (case["id"], seq_value),
        )
        return JSONResponse(status_code=200, content=existing[0])

    segment = get_segment(result["id"])
    publish_segment("segment.created", segment, case["emt_id"])
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
    if segment["status"] != "failed":
        raise APIError(409, "Only failed segments can be retried")

    run("UPDATE transcript_segments SET status = 'pending', error = NULL WHERE id = ?", (segment["id"],))
    segment = get_segment(segment["id"])
    publish_segment("segment.updated", segment, case["emt_id"])
    background_tasks.add_task(transcribe_segment, segment["id"])
    return segment


def transcribe_segment_sync(audio_path: str, prompt: Optional[str]) -> str:
    client = recordings.openai_client
    if not client:
        raise TranscriptionUnavailable()
    kwargs = {"prompt": prompt} if prompt else {}
    with open(audio_path, "rb") as audio_file:
        return client.audio.transcriptions.create(
            file=audio_file,
            model="whisper-1",
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
    """Tail of the preceding segment's transcript, passed to Whisper for continuity across cuts."""
    rows = query(
        """SELECT text FROM transcript_segments
           WHERE case_id = ? AND seq < ? AND status = 'completed' AND text != ''
           ORDER BY seq DESC LIMIT 1""",
        (segment["case_id"], segment["seq"]),
    )
    return rows[0]["text"][-200:] if rows else None


async def transcribe_segment(segment_id: int):
    rows = query(
        """SELECT s.id, s.case_id, s.seq, s.audio_file_path, c.emt_id
           FROM transcript_segments s JOIN cases c ON s.case_id = c.id
           WHERE s.id = ?""",
        (segment_id,),
    )
    if not rows:
        return
    segment = rows[0]
    prompt = previous_segment_text(segment)
    loop = asyncio.get_running_loop()

    text, error_message = None, None
    for attempt in range(1, MAX_TRANSCRIPTION_ATTEMPTS + 1):
        run("UPDATE transcript_segments SET attempts = attempts + 1 WHERE id = ?", (segment_id,))
        try:
            text = await loop.run_in_executor(
                transcription_executor, transcribe_segment_sync, segment["audio_file_path"], prompt
            )
            break
        except Exception as error:
            print(f"❌ Transcription error for segment {segment_id} (attempt {attempt}): {error!r}")
            error_message, transient = describe_transcription_error(error)
            if not transient or attempt == MAX_TRANSCRIPTION_ATTEMPTS:
                break
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)

    if text is not None:
        run(
            "UPDATE transcript_segments SET status = 'completed', text = ?, error = NULL, transcribed_at = ? WHERE id = ?",
            (str(text).strip(), utc_now_iso(), segment_id),
        )
    else:
        run(
            "UPDATE transcript_segments SET status = 'failed', error = ? WHERE id = ?",
            (error_message, segment_id),
        )
    publish_segment("segment.updated", get_segment(segment_id), segment["emt_id"])
