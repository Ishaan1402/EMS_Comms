"""Queue position history (KAN-29): every change to a hospital's inbound order, and its cause.

case_queue.py decides the order from stored case state only (never the clock), so the order can
only change when something is committed. routes/cases.py calls record() after each committed
change to a case; reading the queue never records anything. Each change is one revision of the
hospital's order: a row per case that entered, moved or left, including patients moved
indirectly by someone else's update (cause_case_id says whose change it was). queue_state holds
the last recorded order to compare with.
"""
import json
from typing import Optional

from backend import case_queue
from backend.database import get_db, query, utc_now_iso

# What committed the change; a controlled vocabulary, so exports carry no free text.
CAUSES = {
    "case_opened", "update_added", "eta_changed", "clip_received", "transcript_added", "transcription_failed",
    "transcription_retried", "failure_handled", "assessment_updated", "acknowledged", "arrived", "closed",
    "report_sent", "report_completed", "alert_called", "alert_cancelled", "alert_acknowledged", "server_started",
}

EVENT_COLUMNS = ("e.id, e.hospital_id, e.revision, e.case_id, c.source_case_id, c.ems_unit, e.change, "
                 "e.from_position, e.to_position, e.cause, e.cause_case_id, e.info_version, "
                 "e.preparation_category, e.needs_attention, e.rule_version, e.created_at")


def record(hospital_id: int, ordered: list, cause: str, cause_case_id: Optional[int]) -> list:
    """
    Compare the hospital's order (case_queue.order output) with the last one recorded and store
    the difference as a new revision. Returns the rows written (none if nothing moved).
    """
    if cause not in CAUSES:
        raise ValueError(f"Unknown queue change cause {cause!r}")
    new = [case["id"] for case in ordered]
    by_id = {case["id"]: case for case in ordered}
    with get_db() as conn:
        state = conn.execute("SELECT revision, case_ids FROM queue_state WHERE hospital_id = ?",
                             (hospital_id,)).fetchone()
        old = json.loads(state["case_ids"]) if state else []
        if new == old:
            return []
        revision = (state["revision"] if state else 0) + 1
        now = utc_now_iso()
        changes = [(case_id, "left", old.index(case_id) + 1, None) for case_id in old if case_id not in by_id]
        for position, case_id in enumerate(new, start=1):
            if case_id not in old:
                changes.append((case_id, "entered", None, position))
            elif old.index(case_id) + 1 != position:
                changes.append((case_id, "moved", old.index(case_id) + 1, position))
        rows = []
        for case_id, change, from_position, to_position in changes:
            case = by_id.get(case_id)
            rows.append((
                hospital_id, revision, case_id, change, from_position, to_position, cause, cause_case_id,
                case and case["info_version"], case and case["preparation_category"],
                None if case is None else int(case_queue.needs_attention(case)),
                case_queue.QUEUE_RULE_VERSION, now,
            ))
        conn.executemany(
            """INSERT INTO queue_events (hospital_id, revision, case_id, change, from_position, to_position, cause,
                                         cause_case_id, info_version, preparation_category, needs_attention,
                                         rule_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.execute(
            """INSERT INTO queue_state (hospital_id, revision, case_ids, rule_version, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (hospital_id) DO UPDATE SET revision = excluded.revision,
                   case_ids = excluded.case_ids, rule_version = excluded.rule_version, updated_at = excluded.updated_at""",
            (hospital_id, revision, json.dumps(new), case_queue.QUEUE_RULE_VERSION, now),
        )
    return rows


def events(hospital_id: int, after_revision: int = 0) -> list:
    """A hospital's queue changes after a revision, oldest first."""
    return query(
        f"""SELECT {EVENT_COLUMNS} FROM queue_events e JOIN cases c ON c.id = e.case_id
            WHERE e.hospital_id = ? AND e.revision > ? ORDER BY e.revision, e.id""",
        (hospital_id, after_revision),
    )
