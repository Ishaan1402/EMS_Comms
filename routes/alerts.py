"""Crew alerts on a case (case_alerts.py): the crew calls or cancels them, the hospital acknowledges them."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import case_alerts
from database import get_db, utc_now_iso
from middleware.auth import APIError, require_role
from routes.cases import MAX_CLIENT_ID_LENGTH, get_case, get_case_for_user, publish_case, read_json_body

router = APIRouter()


@router.get("/{case_id}/alerts")
async def list_alerts(case_id: str, current_user: dict = Depends(require_role(["emt", "doctor"]))):
    """Every call, key-time change and cancellation on the case, oldest first."""
    case = get_case_for_user(case_id, current_user)
    return case_alerts.changes(case["id"])


@router.post("/{case_id}/alerts", status_code=201)
async def change_alert(case_id: str, request: Request, current_user: dict = Depends(require_role(["emt"]))):
    """
    The crew calls or cancels an alert on its open case. Body:
      {"alert": "stroke" | "stemi" | "trauma" | "sepsis", "action": "called" | "cancelled",
       "key_time": "2026-10-13T14:10:00Z" or null (not stated), "client_id": "..."}
    Calling an active alert again with a different key time corrects it; the same call again is a
    no-op (200). A retry with the same client_id returns the stored change (200).
    """
    case = get_case_for_user(case_id, current_user)
    body = await read_json_body(request)
    alert, action = body.get("alert"), body.get("action", "called")
    if alert not in case_alerts.ALERTS:
        raise APIError(400, f"alert must be one of {', '.join(case_alerts.ALERTS)}")
    if action not in case_alerts.ACTIONS:
        raise APIError(400, "action must be called or cancelled")
    try:
        key_time = case_alerts.parse_key_time(body.get("key_time")) if action == "called" else None
    except case_alerts.InvalidAlert as error:
        raise APIError(400, str(error))
    client_id = body.get("client_id")
    if client_id is not None and (not isinstance(client_id, str) or not 0 < len(client_id) <= MAX_CLIENT_ID_LENGTH):
        raise APIError(400, "Invalid client_id")

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")  # so a retry can't race the original past the client_id check
        if client_id is not None:
            existing = conn.execute("SELECT id FROM case_alerts WHERE case_id = ? AND client_id = ?",
                                    (case["id"], client_id)).fetchone()
            if existing:
                return JSONResponse(status_code=200, content=_change(case["id"], existing["id"]))
        if conn.execute("SELECT status FROM cases WHERE id = ?", (case["id"],)).fetchone()["status"] != "active":
            raise APIError(409, "Case is closed")
        current = conn.execute(
            "SELECT id, action, key_time FROM case_alerts WHERE case_id = ? AND alert = ? ORDER BY id DESC LIMIT 1",
            (case["id"], alert),
        ).fetchone()
        active = current is not None and current["action"] == "called"
        if action == "cancelled" and not active:
            raise APIError(409, "That alert isn't active")
        if action == "called" and active and current["key_time"] == key_time:
            return JSONResponse(status_code=200, content=_change(case["id"], current["id"]))
        now = utc_now_iso()
        change_id = conn.execute(
            """INSERT INTO case_alerts (case_id, alert, action, key_time, author_id, client_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case["id"], alert, action, key_time, current_user["id"], client_id, now),
        ).lastrowid
        # Screens keep the newest copy of a case, so the case must look newer.
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case["id"]))
    publish_case("case.updated", get_case(case["id"]), "update_added")
    return _change(case["id"], change_id)


@router.post("/{case_id}/alerts/acknowledge")
async def acknowledge_alerts(case_id: str, request: Request, current_user: dict = Depends(require_role(["doctor"]))):
    """
    A hospital user acknowledges the crew's alerts. Body (optional): {"through_alert_id": n}, the
    latest change their screen showed, so a change that arrived after they looked stays
    unacknowledged. Defaults to the latest change.
    """
    case = get_case_for_user(case_id, current_user)
    body = await read_json_body(request)
    alerts = case["crew_alerts"]
    latest = alerts["latest_change_id"]
    if latest is None:
        raise APIError(409, "This case has no crew alerts")
    through = body.get("through_alert_id", latest)
    if isinstance(through, bool) or not isinstance(through, int) or not \
            query_change_exists(case["id"], through):
        raise APIError(400, "through_alert_id must be one of this case's alert changes")
    acknowledged = alerts["acknowledgment"]
    if acknowledged and acknowledged["through_alert_id"] >= through:
        return case
    now = utc_now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO case_alert_acknowledgments (case_id, through_alert_id, user_id, acknowledged_at)
               VALUES (?, ?, ?, ?)""",
            (case["id"], through, current_user["id"], now),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case["id"]))
    case = get_case(case["id"])
    publish_case("case.updated", case, "acknowledged")
    return case


def query_change_exists(case_id: int, change_id: int) -> bool:
    return any(change["id"] == change_id for change in case_alerts.changes(case_id))


def _change(case_id: int, change_id: int) -> dict:
    return next(change for change in case_alerts.changes(case_id) if change["id"] == change_id)
