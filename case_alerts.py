"""Crew alerts: a crew's declared time-critical condition (stroke, STEMI, trauma, sepsis) and its key time.

A crew alert is a person's call, not the AI's. It is stored apart from the reports, so the AI's
input (case_assessment.read_inputs) and the information version the hospital acknowledges are
unchanged by it. The hospital acknowledges alerts separately: each acknowledgment covers every
alert change up to one id, so a later call, key-time change or cancellation needs acknowledging
again.

History is append-only (case_alerts): a call, a re-call with a corrected key time, and a
cancellation are each a row. An alert type is active when its latest row is a call.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from database import query, to_iso

# alert -> (label on screen, what its key time is). Every key time is optional: an unknown one is
# shown as "not stated", never guessed.
ALERTS = {
    "stroke": ("Stroke", "Last known well"),
    "stemi": ("STEMI", "Symptom onset"),
    "trauma": ("Trauma", "Time of injury"),
    "sepsis": ("Sepsis", "Symptom onset"),
}
ACTIONS = ("called", "cancelled")
# Device clocks drift; a key time a little in the future is accepted, a later one is a mistake.
FUTURE_TOLERANCE = timedelta(minutes=5)
OLDEST_KEY_TIME = timedelta(days=7)

CHANGE_SELECT = """
    SELECT a.id, a.case_id, a.alert, a.action, a.key_time, a.author_id, a.client_id, a.created_at,
           u.first_name || ' ' || u.last_name AS author_name
    FROM case_alerts a JOIN users u ON u.id = a.author_id
"""


class InvalidAlert(ValueError):
    pass


def parse_key_time(value, now: Optional[datetime] = None) -> Optional[str]:
    """An ISO-8601 time (naive means UTC) in the case tables' format, or None when not stated."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidAlert("key_time must be an ISO-8601 time or null")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise InvalidAlert("key_time must be an ISO-8601 time or null")
    now = now or datetime.now(timezone.utc)
    if parsed > now + FUTURE_TOLERANCE:
        raise InvalidAlert("key_time can't be in the future")
    if parsed < now - OLDEST_KEY_TIME:
        raise InvalidAlert("key_time is more than 7 days ago")
    return to_iso(parsed)


def describe_change(row: dict) -> dict:
    label, key_time_label = ALERTS[row["alert"]]
    return {**row, "label": label, "key_time_label": key_time_label}


def changes(case_id: int) -> list:
    """A case's alert history, oldest first."""
    return [describe_change(row) for row in query(CHANGE_SELECT + " WHERE a.case_id = ? ORDER BY a.id", (case_id,))]


def active_alerts(rows: list) -> list:
    """The alerts whose latest change is a call, in the order they were first called."""
    latest = {}
    for row in rows:
        latest[row["alert"]] = row
    first_called = {}
    for row in rows:
        first_called.setdefault(row["alert"], row["id"])
    active = [row for row in latest.values() if row["action"] == "called"]
    return sorted(active, key=lambda row: first_called[row["alert"]])


def summaries(case_ids: list) -> dict:
    """
    For each case: {"active": [...], "latest_change_id", "acknowledgment", "unacknowledged"}.
    unacknowledged: a call, change or cancellation the hospital hasn't acknowledged yet.
    """
    result = {case_id: {"active": [], "latest_change_id": None, "acknowledgment": None, "unacknowledged": False}
              for case_id in case_ids}
    if not case_ids:
        return result
    marks = ",".join("?" * len(case_ids))
    rows = query(CHANGE_SELECT + f" WHERE a.case_id IN ({marks}) ORDER BY a.id", tuple(case_ids))
    acks = query(
        f"""SELECT case_id, through_alert_id, acknowledged_at, user_name FROM (
                SELECT k.*, u.first_name || ' ' || u.last_name AS user_name,
                       ROW_NUMBER() OVER (PARTITION BY k.case_id ORDER BY k.through_alert_id DESC, k.id DESC) AS rn
                FROM case_alert_acknowledgments k JOIN users u ON u.id = k.user_id
                WHERE k.case_id IN ({marks})
            ) WHERE rn = 1""",
        tuple(case_ids),
    )
    by_case = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(describe_change(row))
    ack_by_case = {ack["case_id"]: ack for ack in acks}
    for case_id, case_rows in by_case.items():
        latest_id = case_rows[-1]["id"]
        ack = ack_by_case.get(case_id)
        result[case_id] = {
            "active": active_alerts(case_rows),
            "latest_change_id": latest_id,
            "acknowledgment": ack and {"through_alert_id": ack["through_alert_id"],
                                       "acknowledged_at": ack["acknowledged_at"], "user_name": ack["user_name"]},
            "unacknowledged": not ack or ack["through_alert_id"] < latest_id,
        }
    return result
