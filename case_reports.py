"""Reports: what the crew says between "Record report" and "Send", like one radio call.

A report groups the ~8-second clips it was recorded as. Its clips are stored, transcribed and
shown live as they arrive, but they become new patient information only together, once the
report is complete:

- it has ended: the EMT sent it with its clip count, or the case was closed or marked arrived;
- that many clips have arrived, and none is still being transcribed.

Then, if any clip has text, the case's info_version goes up once and every clip with text gets
that version. So the hospital acknowledges, the AI assesses and extraction reads whole reports,
never half of one. A clip uploaded without a report id gets a report of its own that has already
ended, which is how every clip behaved before reports existed.

A clip that finishes after its report completed (a retried failure, say) adds information on its
own, as a clip always did. Every function here takes the caller's connection, so the check runs in
the same transaction as the change that triggered it.
"""
from typing import Optional

# For the AI readers (case_assessment, case_extraction): a clip whose report hasn't completed is not
# information yet. Clips stored before reports existed have no report and keep their versions.
FINISHED_CLIP = ("(transcript_segments.report_id IS NULL OR transcript_segments.report_id IN "
                 "(SELECT id FROM case_reports WHERE completed_at IS NOT NULL))")

# A client id of the reports made for clips sent without one; never a valid device report id.
SINGLE_CLIP_PREFIX = "clip:"


def report_for_clip(conn, case_id: int, report_client_id: Optional[str], clip_client_id: str, now: str) -> int:
    """The report a new clip belongs to, created on first sight. Without a report id, a one-clip report."""
    if report_client_id is None:
        cursor = conn.execute(
            """INSERT INTO case_reports (case_id, client_id, clip_count, started_at, ended_at)
               VALUES (?, ?, 1, ?, ?)""",
            (case_id, SINGLE_CLIP_PREFIX + clip_client_id, now, now),
        )
        return cursor.lastrowid
    return _get_or_create(conn, case_id, report_client_id, now)["id"]


def _get_or_create(conn, case_id: int, client_id: str, now: str):
    conn.execute("INSERT OR IGNORE INTO case_reports (case_id, client_id, started_at) VALUES (?, ?, ?)",
                 (case_id, client_id, now))
    return conn.execute("SELECT * FROM case_reports WHERE case_id = ? AND client_id = ?",
                        (case_id, client_id)).fetchone()


class ClipCountMismatch(Exception):
    pass


def end(conn, case_id: int, client_id: str, clips: int, now: str) -> tuple:
    """
    The EMT sent the report: it has `clips` clips. It can arrive before they do. Sending it again with
    the same count changes nothing; a different count raises ClipCountMismatch.
    Returns (report row, whether the case's information changed).
    """
    report = _get_or_create(conn, case_id, client_id, now)
    if report["clip_count"] is not None:
        if report["clip_count"] != clips:
            raise ClipCountMismatch()
        return report, False
    conn.execute("UPDATE case_reports SET clip_count = ?, ended_at = ? WHERE id = ?", (clips, now, report["id"]))
    changed = settle(conn, report["id"], now)
    return conn.execute("SELECT * FROM case_reports WHERE id = ?", (report["id"],)).fetchone(), changed


def end_all(conn, case_id: int, now: str) -> bool:
    """
    The case closed or arrived: every unfinished report ends with the clips it has. Clips that never
    arrived can't be waited for any more. Returns whether the case's information changed.
    """
    reports = conn.execute("SELECT id FROM case_reports WHERE case_id = ? AND completed_at IS NULL",
                           (case_id,)).fetchall()
    changed = False
    for report in reports:
        conn.execute(
            """UPDATE case_reports SET ended_at = COALESCE(ended_at, ?),
                   clip_count = (SELECT COUNT(*) FROM transcript_segments WHERE report_id = ?)
               WHERE id = ?""",
            (now, report["id"], report["id"]),
        )
        changed = settle(conn, report["id"], now) or changed
    return changed


def settle(conn, report_id: Optional[int], now: str) -> bool:
    """
    Complete the report if it is ready, or version a late clip of a completed one.
    Returns whether the case's information changed (info_version went up).
    """
    if report_id is None:
        return False
    report = conn.execute("SELECT * FROM case_reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        return False
    if report["completed_at"] is None:
        if report["clip_count"] is None:
            return False
        arrived, pending = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(status = 'pending'), 0)
               FROM transcript_segments WHERE report_id = ?""",
            (report_id,),
        ).fetchone()
        if arrived < report["clip_count"] or pending:
            return False
        conn.execute("UPDATE case_reports SET completed_at = ? WHERE id = ?", (now, report_id))
    new_text = conn.execute(
        """SELECT id FROM transcript_segments
           WHERE report_id = ? AND status = 'completed' AND text != '' AND info_version IS NULL""",
        (report_id,),
    ).fetchall()
    if not new_text:
        return False
    case_id = report["case_id"]
    conn.execute("UPDATE cases SET info_version = info_version + 1, last_update_at = ?, updated_at = ? WHERE id = ?",
                 (now, now, case_id))
    version = conn.execute("SELECT info_version FROM cases WHERE id = ?", (case_id,)).fetchone()[0]
    conn.executemany("UPDATE transcript_segments SET info_version = ?, updated_at = ? WHERE id = ?",
                     [(version, now, row["id"]) for row in new_text])
    conn.execute("UPDATE case_reports SET info_version = COALESCE(info_version, ?) WHERE id = ?", (version, report_id))
    return True


def is_single_clip(conn, report_id: Optional[int]) -> bool:
    row = conn.execute("SELECT client_id FROM case_reports WHERE id = ?", (report_id,)).fetchone()
    return row is None or row["client_id"].startswith(SINGLE_CLIP_PREFIX)


def settle_after_restart(conn, now: str) -> list:
    """
    At startup, after interrupted clips were failed: complete the reports that became ready, and end
    those of cases that closed or arrived. Returns the ids of cases whose information changed.
    """
    changed = set()
    for report in conn.execute(
            """SELECT r.id, r.case_id, c.status, c.arrived_at FROM case_reports r JOIN cases c ON c.id = r.case_id
               WHERE r.completed_at IS NULL""").fetchall():
        if report["status"] != "active" or report["arrived_at"]:
            if end_all(conn, report["case_id"], now):
                changed.add(report["case_id"])
        elif settle(conn, report["id"], now):
            changed.add(report["case_id"])
    return sorted(changed)
