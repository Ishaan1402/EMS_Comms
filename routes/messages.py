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

# Mounted at /api/recordings (threads of the older single-recording flow) and /api/cases.
router = APIRouter()
case_router = APIRouter()

MAX_MESSAGE_LENGTH = 2000
MAX_CLIENT_ID_LENGTH = 64
HISTORY_LIMIT = 500
# The stream re-checks the database this often even without a local wake-up,
# so messages written by another process still arrive.
STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0

MESSAGE_COLUMNS = """m.id, m.recording_id, m.case_id, m.sender_id, m.sender_role, m.body, m.client_id, m.created_at,
                     u.first_name AS sender_first_name, u.last_name AS sender_last_name"""

_NON_NEGATIVE_INT = re.compile(r"[0-9]+")


class Thread:
    """A message thread: a case, or a recording from the older single-recording flow."""

    def __init__(self, column: str, thread_id: int):
        self.column = column  # "case_id" or "recording_id"; never user input
        self.id = thread_id

    @property
    def key(self):
        return (self.column, self.id)


# Thread.key -> events of the streams open on that thread; set when a message is posted.
_listeners = defaultdict(set)


def _wake_listeners(thread: Thread):
    for event in _listeners.get(thread.key, ()):
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


def _parse_id(id: str, not_found: str) -> int:
    try:
        return int(id)
    except ValueError:
        raise APIError(404, not_found)


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


def authorize_live_case(user: dict, case_id: int) -> None:
    """
    Same visibility as the case itself: the EMT who owns it and hospital users.
    TODO(KAN-15): limit hospital users to cases routed to their hospital.
    """
    role = user.get("role")
    if role == "emt":
        rows = query('SELECT 1 FROM cases WHERE id = ? AND emt_id = ?', (case_id, user.get("id")))
    elif role == "doctor":
        rows = query('SELECT 1 FROM cases WHERE id = ?', (case_id,))
    else:
        rows = []

    if not rows:
        raise APIError(404, "Case not found")


def _recording_thread(id: str, user: dict) -> Thread:
    recording_id = _parse_id(id, "Recording not found")
    authorize_case(user, recording_id)
    return Thread("recording_id", recording_id)


def _case_thread(id: str, user: dict) -> Thread:
    case_id = _parse_id(id, "Case not found")
    authorize_live_case(user, case_id)
    return Thread("case_id", case_id)


def fetch_messages(thread: Thread, after_id: int = 0, limit: int = HISTORY_LIMIT) -> list:
    """
    Messages for one thread, oldest first. id is the order: it only ever increases.
    Past `limit`, callers page on with after_id (the stream does this on its own).
    """
    return query(
        f"""SELECT {MESSAGE_COLUMNS}
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.{thread.column} = ? AND m.id > ?
            ORDER BY m.id ASC
            LIMIT ?""",
        (thread.id, after_id, limit)
    )


def _fetch_message(message_id: int) -> dict:
    return query(
        f"""SELECT {MESSAGE_COLUMNS}
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.id = ?""",
        (message_id,)
    )[0]


def _history(thread: Thread, after_id: Optional[str]):
    after_id = _parse_after_id(after_id)
    try:
        return {
            thread.column: thread.id,
            "messages": fetch_messages(thread, after_id)
        }
    except Exception as error:
        print(f"Get messages error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting messages"}
        )


async def _post(thread: Thread, request: Request, current_user: dict):
    """
    The thread comes only from the URL; ids in the body are ignored.
    client_id makes retries idempotent.
    """
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
                    'SELECT id, recording_id, case_id FROM messages WHERE sender_id = ? AND client_id = ?',
                    (current_user["id"], client_id)
                ).fetchone()
            if existing and existing[thread.column] != thread.id:
                raise APIError(409, "client_id already used for another case")

            created = existing is None
            if created:
                message_id = conn.execute(
                    f'INSERT INTO messages ({thread.column}, sender_id, sender_role, body, client_id) VALUES (?, ?, ?, ?, ?)',
                    (thread.id, current_user["id"], current_user["role"], text, client_id)
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
        _wake_listeners(thread)
        return message
    return JSONResponse(status_code=200, content=message)


def _sse_event(message: dict) -> str:
    return f"id: {message['id']}\nevent: message\ndata: {json.dumps(message)}\n\n"


def _stream(thread: Thread, request: Request, after_id: Optional[str]):
    """
    Server-sent events: every message on the thread newer than after_id (or the
    Last-Event-ID header), then new ones as they are posted.
    """
    after_id = _parse_after_id(after_id)

    last_event_id = _parse_cursor(request.headers.get("last-event-id"))
    if last_event_id is not None:
        after_id = max(after_id, last_event_id)

    async def events():
        wake = asyncio.Event()
        listeners = _listeners[thread.key]
        listeners.add(wake)
        last_id = after_id
        idle = 0.0
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                wake.clear()
                rows = await run_in_threadpool(fetch_messages, thread, last_id)
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
            if not listeners and _listeners.get(thread.key) is listeners:
                del _listeners[thread.key]

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # no-transform keeps the dev-server proxy from gzip-buffering the stream.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/{id}/messages")
async def get_messages(id: str, request: Request, after_id: Optional[str] = Query(None)):
    """Message history for a recording. after_id returns only newer messages (used to catch up)."""
    thread = _recording_thread(id, get_current_user(request))
    return _history(thread, after_id)


@router.post("/{id}/messages", status_code=201)
async def post_message(id: str, request: Request):
    current_user = get_current_user(request)
    return await _post(_recording_thread(id, current_user), request, current_user)


@router.get("/{id}/messages/stream")
async def stream_messages(id: str, request: Request, after_id: Optional[str] = Query(None)):
    thread = _recording_thread(id, get_current_user(request))
    return _stream(thread, request, after_id)


@case_router.get("/{id}/messages")
async def get_case_messages(id: str, request: Request, after_id: Optional[str] = Query(None)):
    """Message history for a case. after_id returns only newer messages (used to catch up)."""
    thread = _case_thread(id, get_current_user(request))
    return _history(thread, after_id)


@case_router.post("/{id}/messages", status_code=201)
async def post_case_message(id: str, request: Request):
    current_user = get_current_user(request)
    return await _post(_case_thread(id, current_user), request, current_user)


@case_router.get("/{id}/messages/stream")
async def stream_case_messages(id: str, request: Request, after_id: Optional[str] = Query(None)):
    thread = _case_thread(id, get_current_user(request))
    return _stream(thread, request, after_id)
