"""Push the app's case data to SAS Viya (CAS) for evaluation and dashboards.

A background task rebuilds a few flat tables from SQLite every SAS_SYNC_INTERVAL_SECONDS and,
for each table whose contents changed, replaces the shared CAS table through the CAS REST API.
SAS is never in the clinical request path: a failure is logged, shown at GET /api/sas/status,
and retried with backoff while the app carries on.

Tables (named SAS_TABLE_PREFIX + name, default ASC_), keyed by CASE_ID plus SOURCE_CASE_ID /
SOURCE_RUN_ID so they join with the evaluation datasets. No free text leaves the app:
transcripts, typed notes, messages and AI summaries stay in SQLite.
    CASES        one row per case, its current state
    EVENTS       one row per thing that happened to a case, in time order
    ASSESSMENTS  every AI assessment attempt, with tokens, latency and cost
    VITALS       every vital sign reading

Every timestamp comes twice: *_AT is ISO-8601 UTC text, and *_DT is the same moment as a SAS
datetime (seconds since 1960-01-01, UTC) formatted DATETIME, so Visual Analytics can chart it.

Configuration (.env): SAS_VIYA_URL, SAS_CLIENT_ID, SAS_CLIENT_SECRET (an OAuth client with the
client_credentials grant), or instead SAS_ACCESS_TOKEN (a token the SAS admins issued for that
client), SAS_SYNC_ENABLED=1 to run in the server, and optionally
SAS_CAS_SERVER (cas-shared-default), SAS_CASLIB (Public), SAS_TABLE_PREFIX (ASC_),
SAS_SYNC_INTERVAL_SECONDS (10), SAS_SAVE_TABLES (1: also save to disk so tables survive a CAS restart).

    python -m backend.sas_sync check     # sign in, read the CAS server and caslibs, open and close a
                                 # CAS session and run one read-only action; writes no tables
    python -m backend.sas_sync push      # push every table now
    python -m backend.sas_sync csv DIR   # write the tables as CSV files + schema.json; load them as
                                 # yourself with sas/load_csvs.py (no app credentials needed)
"""
import asyncio
import csv
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Optional

import httpx

from backend import database
from backend.database import query, read_snapshot, utc_now_iso

SAS_EPOCH = datetime(1960, 1, 1, tzinfo=timezone.utc)
DATETIME_FORMAT = "DATETIME22.3"
MAX_BACKOFF_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class Settings:
    url: str
    client_id: str
    client_secret: str
    cas_server: str = "cas-shared-default"
    caslib: str = "Public"
    prefix: str = "ASC_"
    interval: float = 10.0
    enabled: bool = False
    save_tables: bool = True
    # Issued by the SAS admins (Oct 2026, valid for the hackathon); used as is instead of signing in.
    access_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        def flag(name, default):
            return os.getenv(name, default).strip().lower() in ("1", "true", "yes")
        return cls(
            url=os.getenv("SAS_VIYA_URL", "").strip().rstrip("/"),
            client_id=os.getenv("SAS_CLIENT_ID", "").strip(),
            client_secret=os.getenv("SAS_CLIENT_SECRET", "").strip(),
            cas_server=os.getenv("SAS_CAS_SERVER", "cas-shared-default").strip(),
            caslib=os.getenv("SAS_CASLIB", "Public").strip(),
            prefix=os.getenv("SAS_TABLE_PREFIX", "ASC_").strip(),
            interval=float(os.getenv("SAS_SYNC_INTERVAL_SECONDS", "10")),
            enabled=flag("SAS_SYNC_ENABLED", "0"),
            save_tables=flag("SAS_SAVE_TABLES", "1"),
            access_token=os.getenv("SAS_ACCESS_TOKEN", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.url and (self.access_token or (self.client_id and self.client_secret)))


# --- Tables -------------------------------------------------------------------------------

def sas_datetime(iso: Optional[str]) -> Optional[float]:
    """ISO-8601 timestamp -> SAS datetime value (seconds since 1960-01-01 UTC), or None."""
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return round((moment - SAS_EPOCH).total_seconds(), 3)


# name -> [(column, "num" | "char", label)]. Columns ending in _DT are SAS datetimes.
CASE_KEYS = [
    ("CASE_ID", "num", "Case ID"),
    ("SOURCE_CASE_ID", "char", "Source case (evaluation dataset)"),
    ("SOURCE_RUN_ID", "char", "Replay run"),
    ("HOSPITAL_CODE", "char", "Destination hospital"),
]


def _time_columns(name: str, label: str) -> list:
    return [(f"{name}_AT", "char", f"{label} (ISO UTC)"), (f"{name}_DT", "num", f"{label} (UTC)")]


TABLES = {
    "CASES": CASE_KEYS + [
        ("EMS_UNIT", "char", "EMS unit"),
        ("STATUS", "char", "Case open/closed"),
        ("OPERATIONAL_STATUS", "char", "Transport status"),
        ("PROCESSING_STATUS", "char", "AI processing status"),
        ("NEEDS_REVIEW", "num", "Needs review (1/0)"),
        ("PREPARATION_CATEGORY", "char", "Current preparation category (derived from ESI_LEVEL)"),
        ("ESI_LEVEL", "num", "Current ESI level (doctor's if set, else AI's)"),
        ("AI_ESI_LEVEL", "num", "AI's suggested ESI level"),
        ("ESI_SET_BY_DOCTOR", "num", "ESI level set by a doctor (1/0)"),
        ("RISK_SCORE", "num", "AI's risk score (1-10)"),
        ("MEANINGFUL_CHANGE", "char", "Current meaningful change"),
        ("ASSESSMENT_OUTDATED", "num", "Assessment based on earlier info (1/0)"),
        ("INFO_VERSION", "num", "Information version"),
        ("ACKNOWLEDGED_VERSION", "num", "Last acknowledged version"),
        ("LATEST_ACKNOWLEDGED", "num", "Latest information acknowledged (1/0)"),
        ("SEGMENT_COUNT", "num", "Audio segments"),
        ("FAILED_SEGMENT_COUNT", "num", "Failed segments not yet handled"),
    ] + _time_columns("STARTED", "Started") + _time_columns("ETA", "ETA")
      + _time_columns("ARRIVED", "Arrived") + _time_columns("CLOSED", "Closed"),
    "EVENTS": [("EVENT_ID", "char", "Event ID")] + CASE_KEYS + [
        ("EVENT_TYPE", "char", "Event type"),
    ] + _time_columns("OCCURRED", "Occurred") + [
        ("SECONDS_SINCE_START", "num", "Seconds since case start"),
        ("INFO_VERSION", "num", "Information version"),
        ("ACTOR_ROLE", "char", "Actor role"),
        ("DETAIL", "char", "Detail"),
        ("VALUE", "num", "Value"),
    ],
    "ASSESSMENTS": [("ASSESSMENT_ID", "num", "Assessment ID")] + CASE_KEYS + [
        ("BASED_ON_VERSION", "num", "Information version assessed"),
        ("BASELINE_VERSION", "num", "Acknowledged version treated as earlier"),
        ("STATUS", "char", "Assessment status"),
        ("PREPARATION_CATEGORY", "char", "Preparation category (derived from the ESI level)"),
        ("ESI_LEVEL", "num", "Suggested ESI level"),
        ("RISK_SCORE", "num", "Risk score (1-10)"),
        ("MEANINGFUL_CHANGE", "char", "Meaningful change"),
        ("REVIEW_REASON", "char", "Review reason"),
        ("ERROR", "char", "Error"),
        ("SCORER_VERSION", "char", "Model / prompt"),
        ("INPUT_TOKENS", "num", "Input tokens"),
        ("OUTPUT_TOKENS", "num", "Output tokens"),
        ("LATENCY_MS", "num", "Model latency (ms)"),
        ("COST_USD", "num", "Estimated cost (USD, list price)"),
    ] + _time_columns("STARTED", "Started") + _time_columns("COMPLETED", "Completed")
        + _time_columns("INPUT_ETA", "Expected arrival used by the assessment"),
    "QUEUE": [("QUEUE_EVENT_ID", "num", "Queue change ID")] + CASE_KEYS + [
        ("REVISION", "num", "Queue revision (per hospital)"),
        ("CHANGE", "char", "entered / moved / left"),
        ("FROM_POSITION", "num", "Position before"),
        ("TO_POSITION", "num", "Position after"),
        ("CAUSE", "char", "What committed the change"),
        ("CAUSE_CASE_ID", "num", "Case whose change caused it"),
        ("INFO_VERSION", "num", "Information version then"),
        ("PREPARATION_CATEGORY", "char", "Preparation category then (derived from ESI_LEVEL)"),
        ("ESI_LEVEL", "num", "ESI level then (doctor's if set, else AI's)"),
        ("NEEDS_ATTENTION", "num", "Needed attention then (1/0)"),
        ("RULE_VERSION", "char", "Queue rule"),
    ] + _time_columns("OCCURRED", "Occurred"),
    "VITALS": [("READING_ID", "num", "Reading ID")] + CASE_KEYS + [
        ("SOURCE", "char", "Source (typed vitals / radio / typed note / case opened)"),
        ("UPDATE_ID", "num", "Update ID"),
        ("SEGMENT_ID", "num", "Transcript segment ID"),
        ("FINDING_ID", "num", "Extracted fact ID"),
        ("NAME", "char", "Vital sign"),
        ("LABEL", "char", "Vital sign label"),
        ("UNIT", "char", "Unit"),
        ("VALUE", "num", "Value"),
    ] + _time_columns("MEASURED", "Measured"),
}


def _bool(value) -> Optional[int]:
    return None if value is None else int(bool(value))


def build_tables() -> dict:
    """
    The tables as {name: list of rows}, each row a dict keyed by column name, all read from one
    consistent snapshot of the database.
    """
    with read_snapshot():
        return _build_tables()


def _build_tables() -> dict:
    from backend.routes.cases import CASE_SELECT, describe_cases

    hospitals = {row["id"]: row["code"] for row in query("SELECT id, code FROM hospitals")}
    cases = describe_cases(query(CASE_SELECT + " ORDER BY c.id"))
    # CASE_KEYS per case; every table exports only rows of these cases.
    keys = {}
    for case in cases:
        keys[case["id"]] = {
            "CASE_ID": case["id"],
            "SOURCE_CASE_ID": case["source_case_id"],
            "SOURCE_RUN_ID": case["source_run_id"],
            "HOSPITAL_CODE": hospitals.get(case["destination_hospital_id"]),
        }
    # Read once: queue changes are both EVENTS rows and the QUEUE table.
    queue_events = query("SELECT * FROM queue_events ORDER BY id")
    return {"CASES": _cases_table(keys, cases), "EVENTS": _events_table(keys, cases, queue_events),
            "ASSESSMENTS": _assessments_table(keys), "QUEUE": _queue_table(keys, queue_events),
            "VITALS": _vitals_table(keys)}


def _with_times(row: dict, **times) -> dict:
    """Adds each timestamp to the row twice: NAME_AT (ISO text) and NAME_DT (SAS datetime)."""
    for name, iso in times.items():
        row[f"{name.upper()}_AT"] = iso
        row[f"{name.upper()}_DT"] = sas_datetime(iso)
    return row


def _cases_table(keys: dict, cases: list) -> list:
    """One row per case, its current state."""
    case_rows = []
    for case in cases:
        assessment = case["current_assessment"] or {}
        ack = case["acknowledgment"]
        case_rows.append(_with_times({
            **keys[case["id"]],
            "EMS_UNIT": case["ems_unit"],
            "STATUS": case["status"],
            "OPERATIONAL_STATUS": case["operational_status"],
            "PROCESSING_STATUS": case["processing"]["status"],
            "NEEDS_REVIEW": _bool(case["processing"]["needs_review"]),
            "PREPARATION_CATEGORY": case["preparation_category"],
            "ESI_LEVEL": case["esi_level"],
            "AI_ESI_LEVEL": case["ai_esi_level"],
            "ESI_SET_BY_DOCTOR": int(bool(case["esi_change"])),
            "RISK_SCORE": case["risk_score"],
            "MEANINGFUL_CHANGE": assessment.get("meaningful_change"),
            "ASSESSMENT_OUTDATED": _bool(case["assessment_is_outdated"]),
            "INFO_VERSION": case["info_version"],
            "ACKNOWLEDGED_VERSION": ack["info_version"] if ack else None,
            "LATEST_ACKNOWLEDGED": _bool(case["latest_update_acknowledged"]),
            "SEGMENT_COUNT": case["segment_count"],
            "FAILED_SEGMENT_COUNT": case["failed_segment_count"],
        }, started=case["started_at"], eta=case["eta_at"], arrived=case["arrived_at"], closed=case["closed_at"]))
    return case_rows


def _events_table(keys: dict, cases: list, queue_events: list) -> list:
    """One row per thing that happened to a case, in time order."""
    started = {}
    for case in cases:
        started[case["id"]] = sas_datetime(case["started_at"])
    events = []

    def event(event_id, case_id, event_type, occurred_at, info_version=None, actor_role=None, detail=None, value=None):
        if case_id not in keys or not occurred_at:
            return
        occurred = sas_datetime(occurred_at)
        start = started.get(case_id)
        events.append(_with_times({
            "EVENT_ID": event_id,
            **keys[case_id],
            "EVENT_TYPE": event_type,
            "SECONDS_SINCE_START": None if occurred is None or start is None else round(occurred - start, 3),
            "INFO_VERSION": info_version,
            "ACTOR_ROLE": actor_role,
            "DETAIL": detail,
            "VALUE": value,
        }, occurred=occurred_at))

    for case in cases:
        event(f"case:{case['id']}:started", case["id"], "case_started", case["started_at"], 1, "emt")
        event(f"case:{case['id']}:arrived", case["id"], "case_arrived", case["arrived_at"])
        event(f"case:{case['id']}:closed", case["id"], "case_closed", case["closed_at"], actor_role="emt")
    for row in query("""SELECT cu.id, cu.case_id, cu.info_version, cu.kind, cu.created_at, u.role
                        FROM case_updates cu JOIN users u ON u.id = cu.author_id"""):
        event(f"update:{row['id']}", row["case_id"], "update_added", row["created_at"],
              row["info_version"], row["role"], row["kind"])
    for row in query("""SELECT s.id, s.case_id, s.seq, s.is_preview, s.status, s.error, s.duration_ms, s.info_version,
                               s.created_at, s.transcribed_at, s.failed_at, s.updated_at, s.dismissed_at,
                               u.role AS dismissed_by_role
                        FROM transcript_segments s LEFT JOIN users u ON u.id = s.dismissed_by"""):
        prefix = "live draft " if row["is_preview"] else ""
        detail = f"{prefix}seq {row['seq']}"
        event(f"segment:{row['id']}:uploaded", row["case_id"], "segment_uploaded", row["created_at"],
              actor_role="emt", detail=detail, value=row["duration_ms"])
        if row["status"] == "completed":
            event(f"segment:{row['id']}:transcribed", row["case_id"], "segment_transcribed", row["transcribed_at"],
                  row["info_version"], detail=detail)
        # failed_at survives a later retry; before it existed, a failed row's updated_at was the failure time.
        failed_at = row["failed_at"] or (row["updated_at"] if row["status"] == "failed" and not row["dismissed_at"] else None)
        if failed_at:
            event(f"segment:{row['id']}:failed", row["case_id"], "segment_failed", failed_at,
                  detail=f"{prefix}{row['error']}" if row["error"] else prefix.strip() or None)
        event(f"segment:{row['id']}:dismissed", row["case_id"], "segment_dismissed", row["dismissed_at"],
              actor_role=row["dismissed_by_role"], detail=detail)
    for row in query("""SELECT id, case_id, based_on_version, status, preparation_category, error,
                               latency_ms, completed_at FROM risk_assessments WHERE completed_at IS NOT NULL"""):
        event(f"assessment:{row['id']}", row["case_id"], f"assessment_{row['status']}", row["completed_at"],
              row["based_on_version"], detail=row["preparation_category"] or row["error"], value=row["latency_ms"])
    for row in query("""SELECT a.id, a.case_id, a.info_version, a.acknowledged_at, u.role
                        FROM case_acknowledgments a JOIN users u ON u.id = a.user_id"""):
        event(f"ack:{row['id']}", row["case_id"], "acknowledged", row["acknowledged_at"], row["info_version"], row["role"])
    # A doctor setting the ESI level; VALUE is the level, empty when they went back to the AI's suggestion.
    for row in query("""SELECT e.id, e.case_id, e.esi_level, e.created_at, u.role
                        FROM case_esi_changes e JOIN users u ON u.id = e.user_id"""):
        event(f"esi:{row['id']}", row["case_id"], "esi_changed", row["created_at"], actor_role=row["role"],
              value=row["esi_level"])
    # Crew alerts (case_alerts.py), which order the queue under rule draft-4. DETAIL is the alert
    # (stroke / stemi / trauma / sepsis, never free text); its key time is an event of its own, at that time.
    for row in query("SELECT id, case_id, alert, action, key_time, created_at FROM case_alerts"):
        event(f"alert:{row['id']}", row["case_id"], f"alert_{row['action']}", row["created_at"],
              actor_role="emt", detail=row["alert"])
        if row["action"] == "called":
            event(f"alert:{row['id']}:key_time", row["case_id"], "alert_key_time", row["key_time"], detail=row["alert"])
    for row in query("""SELECT k.id, k.case_id, k.acknowledged_at, a.alert, u.role FROM case_alert_acknowledgments k
                        JOIN case_alerts a ON a.id = k.through_alert_id JOIN users u ON u.id = k.user_id"""):
        event(f"alert_ack:{row['id']}", row["case_id"], "alert_acknowledged", row["acknowledged_at"],
              actor_role=row["role"], detail=row["alert"])
    for row in query("SELECT id, case_id, sender_role, created_at FROM messages WHERE case_id IS NOT NULL"):
        event(f"message:{row['id']}", row["case_id"], "message_sent", row["created_at"], actor_role=row["sender_role"])
    # Queue position changes (queue_history.py); DETAIL is the cause, VALUE the new position.
    for row in queue_events:
        event(f"queue:{row['id']}", row["case_id"], f"queue_{row['change']}", row["created_at"],
              row["info_version"], detail=row["cause"], value=row["to_position"])
    for row in query("""SELECT r.id, r.case_id, r.to_version, r.status, r.error, r.latency_ms, r.completed_at,
                               (SELECT COUNT(*) FROM case_findings f WHERE f.run_id = r.id) AS facts
                        FROM extraction_runs r WHERE r.completed_at IS NOT NULL"""):
        event(f"extraction:{row['id']}", row["case_id"], f"extraction_{row['status']}", row["completed_at"],
              row["to_version"], detail=row["error"] or f"{row['facts']} facts", value=row["latency_ms"])
    events.sort(key=lambda e: (e["OCCURRED_DT"] if e["OCCURRED_DT"] is not None else 0, e["EVENT_ID"]))
    return events


def _assessments_table(keys: dict) -> list:
    """Every AI assessment attempt, with tokens, latency and cost."""
    return [_with_times({
        "ASSESSMENT_ID": row["id"],
        **keys[row["case_id"]],
        "BASED_ON_VERSION": row["based_on_version"],
        "BASELINE_VERSION": row["baseline_version"],
        "STATUS": row["status"],
        "PREPARATION_CATEGORY": row["preparation_category"],
        "ESI_LEVEL": row["esi_level"],
        "RISK_SCORE": row["risk_score"],
        "MEANINGFUL_CHANGE": row["meaningful_change"],
        "REVIEW_REASON": row["review_reason"],
        "ERROR": row["error"],
        "SCORER_VERSION": row["scorer_version"],
        "INPUT_TOKENS": row["input_tokens"],
        "OUTPUT_TOKENS": row["output_tokens"],
        "LATENCY_MS": row["latency_ms"],
        "COST_USD": row["cost_usd"],
    }, started=row["started_at"], completed=row["completed_at"], input_eta=row["input_eta_at"] or None)
        for row in query("SELECT * FROM risk_assessments ORDER BY id") if row["case_id"] in keys]


def _queue_table(keys: dict, queue_events: list) -> list:
    """Every change to a hospital's queue order."""
    return [_with_times({
        "QUEUE_EVENT_ID": row["id"], **keys[row["case_id"]],
        "REVISION": row["revision"], "CHANGE": row["change"],
        "FROM_POSITION": row["from_position"], "TO_POSITION": row["to_position"],
        "CAUSE": row["cause"], "CAUSE_CASE_ID": row["cause_case_id"], "INFO_VERSION": row["info_version"],
        "PREPARATION_CATEGORY": row["preparation_category"], "ESI_LEVEL": row["esi_level"],
        "NEEDS_ATTENTION": row["needs_attention"],
        "RULE_VERSION": row["rule_version"],
    }, occurred=row["created_at"]) for row in queue_events if row["case_id"] in keys]


def _vitals_table(keys: dict) -> list:
    """
    Typed vitals and vitals extracted from the reports (case_extraction.py), in time order.
    The extracted ones carry their source but not the spoken words or qualifier (no free text).
    """
    from backend.case_assessment import VITAL_SIGNS

    readings = [(row["measured_at"], row["case_id"], row["name"], row["value"],
                 {"READING_ID": row["id"], "SOURCE": "typed vitals", "UPDATE_ID": row["update_id"]})
                for row in query("SELECT id, case_id, update_id, name, value, measured_at FROM vital_readings")]
    readings += [(row["recorded_at"], row["case_id"], row["vital_name"], row["vital_value"],
                  {"SOURCE": "radio" if row["segment_id"] else "typed note" if row["update_id"] else "case opened",
                   "UPDATE_ID": row["update_id"], "SEGMENT_ID": row["segment_id"], "FINDING_ID": row["id"]})
                 for row in query("""SELECT id, case_id, segment_id, update_id, vital_name, vital_value, recorded_at
                                     FROM case_findings WHERE finding_type = 'vital' AND run_id IS NOT NULL""")]
    readings.sort(key=lambda r: (r[0] or "", r[1]))
    vital_rows = []
    for measured_at, case_id, name, value, ids in readings:
        if case_id not in keys:
            continue
        label, unit, _, _ = VITAL_SIGNS.get(name, (name, "", 0, 0))
        vital_rows.append(_with_times({
            "READING_ID": None, **keys[case_id], "UPDATE_ID": None, "SEGMENT_ID": None, "FINDING_ID": None, **ids,
            "NAME": name, "LABEL": label, "UNIT": unit, "VALUE": value,
        }, measured=measured_at))
    return vital_rows


# Every helper below takes the column lists of another set of tables as `columns` (same shape as
# TABLES); evals/benchmark/to_sas.py pushes the benchmark results through them.

def table_vars(name: str, columns: Optional[dict] = None) -> list:
    """CAS import options for each column, in file order: fixed types so empty columns stay numeric."""
    return [{"name": column, "type": "double" if kind == "num" else "varchar", "label": label}
            for column, kind, label in (columns or TABLES)[name]]


def table_formats(name: str, columns: Optional[dict] = None) -> list:
    return [{"name": column, "format": DATETIME_FORMAT} for column, _, _ in (columns or TABLES)[name]
            if column.endswith("_DT")]


def schema(tables: dict, prefix: str, columns: Optional[dict] = None) -> dict:
    """What sas/load_csvs.py needs to load the CSV files exactly as the sync would."""
    return {"tables": {prefix + name: {"rows": len(rows), "vars": table_vars(name, columns),
                                       "formats": table_formats(name, columns)}
                       for name, rows in tables.items()}}


def to_csv(name: str, rows: list, columns: Optional[dict] = None) -> bytes:
    """CSV with the table's columns in order; missing values are empty."""
    columns = [column for column, _, _ in (columns or TABLES)[name]]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(column) is None else row[column] for column in columns])
    return out.getvalue().encode("utf-8")


# --- CAS REST client -------------------------------------------------------------------------

class SasError(Exception):
    """A failed call to SAS. The message is safe to show: it never includes credentials."""


class CasClient:
    """Client-credentials sign-in plus the few CAS REST calls the sync needs."""

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.settings = settings
        # Keep no cookies: CAS sets tkhttp-id on its first reply, and a client-credentials request that
        # sends it back is refused (HTTP 403) for every later action and for closing the session.
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport,
                                       cookies=CookieJar(DefaultCookiePolicy(allowed_domains=[])))
        self._token = None
        self._token_expires = 0.0

    @property
    def cas_url(self) -> str:
        return f"{self.settings.url}/{self.settings.cas_server}-http"

    async def close(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict:
        if self.settings.access_token:
            return {"Authorization": f"Bearer {self.settings.access_token}", "Accept": "application/json"}
        if not self._token or time.monotonic() > self._token_expires:
            response = await self._http.post(
                f"{self.settings.url}/SASLogon/oauth/token",
                data={"grant_type": "client_credentials"},
                auth=(self.settings.client_id, self.settings.client_secret),
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                raise SasError(f"sign-in failed: HTTP {response.status_code}")
            body = response.json()
            self._token = body["access_token"]
            # Renew a minute early; tokens from this client last much longer than a sync interval.
            self._token_expires = time.monotonic() + max(60, float(body.get("expires_in", 3600)) - 60)
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def _call(self, method: str, url: str, what: str, **kwargs) -> httpx.Response:
        headers = {**await self._headers(), **kwargs.pop("headers", {})}
        response = await self._http.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise SasError(f"{what}: HTTP {response.status_code}")
        return response

    async def get(self, path: str, what: str) -> dict:
        return (await self._call("GET", self.settings.url + path, what)).json()

    async def open_session(self) -> str:
        response = await self._call("POST", f"{self.cas_url}/cas/sessions", "open CAS session")
        return response.json()["session"]

    async def close_session(self, session: str) -> None:
        await self._call("DELETE", f"{self.cas_url}/cas/sessions/{session}", "close CAS session")

    @staticmethod
    def _check(response: httpx.Response, what: str) -> dict:
        """CAS reports action failures in the body: disposition.severity == "Error" (warnings pass)."""
        body = response.json()
        disposition = body.get("disposition") or {}
        if disposition.get("severity") == "Error":
            reason = disposition.get("formattedStatus") or disposition.get("reason") or "failed"
            raise SasError(f"{what}: {reason}")
        return body

    async def action(self, session: str, action: str, params: dict) -> dict:
        response = await self._call(
            "POST", f"{self.cas_url}/cas/sessions/{session}/actions/{action}", action,
            headers={"Content-Type": "application/json"}, content=json.dumps(params),
        )
        return self._check(response, action)

    async def upload_csv(self, session: str, table: str, name: str, data: bytes,
                         columns: Optional[dict] = None) -> dict:
        """table.upload into a session-scope table, with every column's type and label fixed."""
        params = {
            "casOut": {"caslib": self.settings.caslib, "name": table, "replace": True},
            "importOptions": {"fileType": "CSV", "vars": table_vars(name, columns)},
        }
        response = await self._call(
            "PUT", f"{self.cas_url}/cas/sessions/{session}/actions/table.upload", f"upload {table}",
            headers={"Content-Type": "text/plain", "JSON-Parameters": json.dumps(params)}, content=data,
        )
        return self._check(response, f"upload {table}")

    async def replace_tables(self, tables: dict, columns: Optional[dict] = None) -> None:
        """
        Replace each shared table with new contents ({name: csv bytes}). Upload to a staging table,
        then swap it in, so a failed upload leaves the old table. Nothing to upload (no rows) leaves
        the shared table as it is: an empty or new database must never wipe published data.
        CAS can't promote over an existing table, so the old one is dropped first; if the promote
        then fails, the last saved copy (.sashdat) is loaded back.
        """
        caslib = self.settings.caslib
        tables = {name: data for name, data in tables.items() if data}
        if not tables:
            return
        session = await self.open_session()
        try:
            for name, data in tables.items():
                target = self.settings.prefix + name
                stage = f"{target}_STAGE"
                await self.upload_csv(session, stage, name, data, columns)
                formats = table_formats(name, columns)
                if formats:
                    await self.action(session, "table.alterTable", {"caslib": caslib, "name": stage, "columns": formats})
                await self.action(session, "table.dropTable", {"caslib": caslib, "name": target, "quiet": True})
                try:
                    await self.action(session, "table.promote", {
                        "caslib": caslib, "name": stage, "target": target, "targetLib": caslib, "drop": True,
                    })
                except SasError as error:
                    await self._restore_saved(session, target, error)
                    raise
                if self.settings.save_tables:
                    await self.action(session, "table.save", {
                        "table": {"caslib": caslib, "name": target}, "caslib": caslib,
                        "name": f"{target}.sashdat", "replace": True,
                    })
        finally:
            try:
                await self.close_session(session)
            except Exception as error:  # don't hide the real failure; the session times out on its own
                print(f"⚠️  SAS: could not close CAS session: {error}")

    async def _restore_saved(self, session: str, target: str, error: "SasError") -> None:
        """After a failed promote: put the last saved copy of the shared table back, if there is one."""
        if not self.settings.save_tables:
            return
        caslib = self.settings.caslib
        try:
            await self.action(session, "table.loadTable", {
                "caslib": caslib, "path": f"{target}.sashdat",
                "casOut": {"caslib": caslib, "name": target, "promote": True},
            })
        except SasError as restore_error:
            raise SasError(f"{error}; restoring {target} from its saved copy also failed: {restore_error}") from error
        print(f"⚠️  SAS: {target} restored from its last saved copy after: {error}")


# --- Background sync -------------------------------------------------------------------------

class SasSync:
    """Pushes changed tables on an interval; never raises into the app."""

    def __init__(self, settings: Optional[Settings] = None, client: Optional[CasClient] = None):
        self.settings = settings or Settings.from_env()
        self._client = client
        self._hashes = {}
        self._task = None
        self._failures = 0
        self.sleep = asyncio.sleep  # replaceable in tests
        self.status = {
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "tables": {},
        }

    @property
    def client(self) -> CasClient:
        if self._client is None:
            self._client = CasClient(self.settings)
        return self._client

    async def push(self, force: bool = False) -> list:
        """Push tables whose contents changed (all of them with force). Returns the names pushed."""
        tables = await asyncio.get_running_loop().run_in_executor(None, build_tables)
        # A table with no rows is not sent: the shared copy (and dashboards on it) stays as it was.
        payloads = {name: to_csv(name, rows) for name, rows in tables.items() if rows}
        for name in tables:
            if not tables[name]:
                self.status["tables"].setdefault(self.settings.prefix + name, {})["skipped"] = "no rows"
        changed = {name: data for name, data in payloads.items()
                   if force or self._hashes.get(name) != hashlib.sha256(data).hexdigest()}
        self.status["last_attempt_at"] = utc_now_iso()
        if changed:
            await self.client.replace_tables(changed)
            now = utc_now_iso()
            for name, data in changed.items():
                self._hashes[name] = hashlib.sha256(data).hexdigest()
                self.status["tables"][self.settings.prefix + name] = {"rows": len(tables[name]), "pushed_at": now}
        self.status["last_success_at"] = utc_now_iso()
        self.status["last_error"] = None
        return list(changed)

    def next_delay(self) -> float:
        """The interval after a success; doubling up to MAX_BACKOFF_SECONDS after failures."""
        if not self._failures:
            return self.settings.interval
        return min(self.settings.interval * 2 ** self._failures, MAX_BACKOFF_SECONDS)

    async def run_forever(self) -> None:
        while True:
            try:
                pushed = await self.push()
                if pushed:
                    print(f"✅ SAS: pushed {', '.join(pushed)}")
                self._failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failures += 1
                message = str(error) if isinstance(error, SasError) else f"{type(error).__name__}: {error}"
                self.status["last_error"] = message
                print(f"⚠️  SAS sync failed ({self._failures} in a row): {message}")
            await self.sleep(self.next_delay())

    def start(self) -> None:
        if self.settings.enabled and self.settings.configured and self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run_forever())
            print(f"🔄 SAS sync on: every {self.settings.interval:g}s to "
                  f"{self.settings.caslib}.{self.settings.prefix}* on {self.settings.url}")
        elif self.settings.enabled:
            print("⚠️  SAS_SYNC_ENABLED is set but SAS_VIYA_URL and the client ID/secret (or SAS_ACCESS_TOKEN) are missing")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.close()
            self._client = None

    def describe(self) -> dict:
        """Status for GET /api/sas/status; no credentials."""
        return {
            "enabled": self.settings.enabled,
            "configured": self.settings.configured,
            "running": self._task is not None,
            "viya_url": self.settings.url or None,
            "caslib": self.settings.caslib,
            "interval_seconds": self.settings.interval,
            "consecutive_failures": self._failures,
            **self.status,
        }


sync = SasSync()


async def check(settings: Settings) -> list:
    """Read-only connectivity check: sign in, read the CAS server and its caslibs."""
    client = CasClient(settings)
    try:
        lines = []
        me = await client.get("/identities/users/@currentUser", "who am I")
        lines.append(f"signed in as {me.get('name') or me.get('id')}")
        server = await client.get(f"/casManagement/servers/{settings.cas_server}", "CAS server")
        lines.append(f"CAS server {server.get('name')} reachable")
        caslibs = await client.get(f"/casManagement/servers/{settings.cas_server}/caslibs?limit=1000", "caslibs")
        names = [item["name"] for item in caslibs.get("items", [])]
        found = settings.caslib in names
        lines.append(f"caslib {settings.caslib}: {'found' if found else 'NOT FOUND'} (visible: {', '.join(names)})")
        # Where this client has been refused (HTTP 403): opening a session, then any action in it.
        # serverStatus only reads; nothing here creates or changes a table.
        try:
            session = await client.open_session()
        except SasError as error:
            lines.append(f"CAS session: {error}")
            return lines
        # Every outcome of the action, including a network error, still tries to close the session.
        try:
            await client.action(session, "builtins.serverStatus", {})
            lines.append("CAS action: ok")
        except (SasError, httpx.HTTPError) as error:
            lines.append(f"CAS action: {error or type(error).__name__}")
        finally:
            try:
                await client.close_session(session)
                lines.append("CAS session close: ok")
            except (SasError, httpx.HTTPError) as error:
                lines.append(f"CAS session close: {error or type(error).__name__}")
        return lines
    finally:
        await client.close()


def main(argv: list) -> int:
    settings = Settings.from_env()
    command = argv[1] if len(argv) > 1 else ""
    if command == "csv" and len(argv) == 3:
        out = Path(argv[2])
        out.mkdir(parents=True, exist_ok=True)
        tables = build_tables()
        for name, rows in tables.items():
            (out / f"{settings.prefix}{name}.csv").write_bytes(to_csv(name, rows))
            print(f"{out / (settings.prefix + name)}.csv: {len(rows)} rows")
        (out / "schema.json").write_text(json.dumps(schema(tables, settings.prefix), indent=2) + "\n")
        print(f"{out / 'schema.json'}: column types and formats for sas/load_csvs.py")
        return 0
    if command not in ("check", "push"):
        print(__doc__)
        return 2
    if not settings.configured:
        print("Set SAS_VIYA_URL and SAS_CLIENT_ID + SAS_CLIENT_SECRET (or SAS_ACCESS_TOKEN) in .env")
        return 1
    try:
        if command == "check":
            for line in asyncio.run(check(settings)):
                print(line)
            return 0

        async def push_all():
            runner = SasSync(settings)
            try:
                return await runner.push(force=True), runner.status
            finally:
                await runner.stop()
        pushed, status = asyncio.run(push_all())
        for table, info in status["tables"].items():
            print(f"{settings.caslib}.{table}: {info['rows']} rows")
        return 0
    except SasError as error:
        print(f"SAS: {error}")
        return 1


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    database.DB_PATH = database.configured_db_path()  # .env may name a different database
    sys.exit(main(sys.argv))
