from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from collections import defaultdict
from typing import Optional
import asyncio
import json
import re
import sqlite3
from database import query, get_db
from middleware.auth import get_current_user, APIError

router = APIRouter()

MAX_MESSAGE_LENGTH = 2000
MAX_CLIENT_ID_LENGTH = 64
HISTORY_LIMIT = 500
# The stream re-checks the database this often even without a local wake-up,
# so messages written by another process still arrive.
STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0

MESSAGE_COLUMNS = """m.id, m.recording_id, m.sender_id, m.sender_role, m.body, m.client_id, m.created_at,
                     u.first_name AS sender_first_name, u.last_name AS sender_last_name"""

_NON_NEGATIVE_INT = re.compile(r"[0-9]+")

# recording_id -> events of the streams open on that case; set when a message is posted.
_listeners = defaultdict(set)


def _wake_listeners(recording_id: int):
    for event in _listeners.get(recording_id, ()):
        event.set()


def _parse_cursor(value: Optional[str]) -> Optional[int]:
    """A message id cursor, or None if absent/malformed. ASCII digits only ("²".isdigit() is True)."""
    if value is None or not _NON_NEGATIVE_INT.fullmatch(value):
        return None
    return int(value)


def _parse_after_id(value: Optional[str]) -> int:
    if value is None or value == "":
        return 0
    after_id = _parse_cursor(value)
    if after_id is None:
        raise APIError(400, "after_id must be a non-negative integer")
    return after_id


def _parse_recording_id(id: str) -> int:
    try:
        return int(id)
    except ValueError:
        raise APIError(404, "Recording not found")


def authorize_case(user: dict, recording_id: int) -> None:
    """
    The EMT who recorded the case and the doctors notified about it may use its messages.
    Everyone else gets the same 404 as a missing case, so case ids can't be probed.
    """
    role = user.get("role")
    if role == "emt":
        rows = query(
            'SELECT 1 FROM recordings WHERE id = ? AND emt_id = ?',
            (recording_id, user.get("id"))
        )
    elif role == "doctor":
        rows = query(
            'SELECT 1 FROM notifications WHERE recording_id = ? AND doctor_id = ? LIMIT 1',
            (recording_id, user.get("id"))
        )
    else:
        rows = []

    if not rows:
        raise APIError(404, "Recording not found")


def fetch_messages(recording_id: int, after_id: int = 0, limit: int = HISTORY_LIMIT) -> list:
    """
    Messages for one case, oldest first. id is the order: it only ever increases.
    Past `limit`, callers page on with after_id (the stream does this on its own).
    """
    return query(
        f"""SELECT {MESSAGE_COLUMNS}
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.recording_id = ? AND m.id > ?
            ORDER BY m.id ASC
            LIMIT ?""",
        (recording_id, after_id, limit)
    )


def _fetch_message(message_id: int) -> dict:
    return query(
        f"""SELECT {MESSAGE_COLUMNS}
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.id = ?""",
        (message_id,)
    )[0]


@router.get("/{id}/messages")
async def get_messages(
    id: str,
    request: Request,
    after_id: Optional[str] = Query(None)
):
    """Message history for a case. after_id returns only newer messages (used to catch up)."""
    current_user = get_current_user(request)
    recording_id = _parse_recording_id(id)
    authorize_case(current_user, recording_id)
    after_id = _parse_after_id(after_id)

    try:
        return {
            "recording_id": recording_id,
            "messages": fetch_messages(recording_id, after_id)
        }
    except Exception as error:
        print(f"Get messages error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting messages"}
        )


@router.post("/{id}/messages", status_code=201)
async def post_message(id: str, request: Request):
    """
    Send a message on a case. The case comes only from the URL; a recording_id
    in the body is ignored. client_id makes retries idempotent.
    """
    current_user = get_current_user(request)
    recording_id = _parse_recording_id(id)
    authorize_case(current_user, recording_id)

    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise APIError(400, "Invalid JSON")
    if not isinstance(body, dict):
        raise APIError(400, "Invalid JSON")

    text = body.get("body")
    if not isinstance(text, str) or not text.strip():
        raise APIError(400, "Message body is required")
    text = text.strip()
    if len(text) > MAX_MESSAGE_LENGTH:
        raise APIError(400, f"Message must be at most {MAX_MESSAGE_LENGTH} characters")

    client_id = body.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or not 0 < len(client_id) <= MAX_CLIENT_ID_LENGTH):
        raise APIError(400, "Invalid client_id")

    try:
        with get_db() as conn:
            # Take the write lock before the client_id lookup so two concurrent
            # retries can't both miss it and race to insert.
            conn.execute("BEGIN IMMEDIATE")
            existing = None
            if client_id is not None:
                existing = conn.execute(
                    'SELECT id, recording_id FROM messages WHERE sender_id = ? AND client_id = ?',
                    (current_user["id"], client_id)
                ).fetchone()
            if existing and existing["recording_id"] != recording_id:
                raise APIError(409, "client_id already used for another case")

            created = existing is None
            if created:
                message_id = conn.execute(
                    'INSERT INTO messages (recording_id, sender_id, sender_role, body, client_id) VALUES (?, ?, ?, ?, ?)',
                    (recording_id, current_user["id"], current_user["role"], text, client_id)
                ).lastrowid
            else:
                message_id = existing["id"]

        message = _fetch_message(message_id)
    except APIError:
        raise
    except sqlite3.IntegrityError as error:
        print(f"Post message integrity error: {error}")
        raise APIError(400, "Message could not be saved")
    except Exception as error:
        print(f"Post message error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error sending message"}
        )

    if created:
        _wake_listeners(recording_id)
        return message
    return JSONResponse(status_code=200, content=message)


def _sse_event(message: dict) -> str:
    return f"id: {message['id']}\nevent: message\ndata: {json.dumps(message)}\n\n"


@router.get("/{id}/messages/stream")
async def stream_messages(
    id: str,
    request: Request,
    after_id: Optional[str] = Query(None)
):
    """
    Server-sent events: every message on the case newer than after_id (or the
    Last-Event-ID header), then new ones as they are posted.
    """
    current_user = get_current_user(request)
    recording_id = _parse_recording_id(id)
    authorize_case(current_user, recording_id)
    after_id = _parse_after_id(after_id)

    last_event_id = _parse_cursor(request.headers.get("last-event-id"))
    if last_event_id is not None:
        after_id = max(after_id, last_event_id)

    async def events():
        wake = asyncio.Event()
        listeners = _listeners[recording_id]
        listeners.add(wake)
        last_id = after_id
        idle = 0.0
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                wake.clear()
                rows = await run_in_threadpool(fetch_messages, recording_id, last_id)
                for row in rows:
                    last_id = row["id"]
                    yield _sse_event(row)
                if rows:
                    idle = 0.0
                    continue

                try:
                    await asyncio.wait_for(wake.wait(), timeout=STREAM_POLL_SECONDS)
                    idle = 0.0
                except asyncio.TimeoutError:
                    idle += STREAM_POLL_SECONDS
                    if idle >= STREAM_HEARTBEAT_SECONDS:
                        idle = 0.0
                        yield ": ping\n\n"
        finally:
            listeners.discard(wake)
            if not listeners and _listeners.get(recording_id) is listeners:
                del _listeners[recording_id]

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # no-transform keeps the dev-server proxy from gzip-buffering the stream.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        }
    )
