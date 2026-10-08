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
client_credentials grant), SAS_SYNC_ENABLED=1 to run in the server, and optionally
SAS_CAS_SERVER (cas-shared-default), SAS_CASLIB (Public), SAS_TABLE_PREFIX (ASC_),
SAS_SYNC_INTERVAL_SECONDS (10), SAS_SAVE_TABLES (1: also save to disk so tables survive a CAS restart).

    python -m sas_sync check     # sign in and read the CAS server and caslibs; writes nothing
    python -m sas_sync push      # push every table now
    python -m sas_sync csv DIR   # write the tables as CSV files + schema.json; load them as
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
from pathlib import Path
from typing import Optional

import httpx

from database import query, utc_now_iso

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
        )

    @property
    def configured(self) -> bool:
        return bool(self.url and self.client_id and self.client_secret)


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
        ("PREPARATION_CATEGORY", "char", "Current preparation category"),
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
        ("PREPARATION_CATEGORY", "char", "Preparation category"),
        ("MEANINGFUL_CHANGE", "char", "Meaningful change"),
        ("REVIEW_REASON", "char", "Review reason"),
        ("ERROR", "char", "Error"),
        ("SCORER_VERSION", "char", "Model / prompt"),
        ("INPUT_TOKENS", "num", "Input tokens"),
        ("OUTPUT_TOKENS", "num", "Output tokens"),
        ("LATENCY_MS", "num", "Model latency (ms)"),
        ("COST_USD", "num", "Estimated cost (USD, list price)"),
    ] + _time_columns("STARTED", "Started") + _time_columns("COMPLETED", "Completed"),
    "VITALS": [("READING_ID", "num", "Reading ID")] + CASE_KEYS + [
        ("UPDATE_ID", "num", "Update ID"),
        ("NAME", "char", "Vital sign"),
        ("LABEL", "char", "Vital sign label"),
        ("UNIT", "char", "Unit"),
        ("VALUE", "num", "Value"),
    ] + _time_columns("MEASURED", "Measured"),
}


def _bool(value) -> Optional[int]:
    return None if value is None else int(bool(value))


def build_tables() -> dict:
    """The four tables as {name: list of rows}, each row a dict keyed by column name."""
    from case_assessment import VITAL_SIGNS
    from routes.cases import CASE_SELECT, describe_cases

    hospitals = {row["id"]: row["code"] for row in query("SELECT id, code FROM hospitals")}
    cases = describe_cases(query(CASE_SELECT + " ORDER BY c.id"))
    keys = {}
    started = {}
    for case in cases:
        keys[case["id"]] = {
            "CASE_ID": case["id"],
            "SOURCE_CASE_ID": case["source_case_id"],
            "SOURCE_RUN_ID": case["source_run_id"],
            "HOSPITAL_CODE": hospitals.get(case["destination_hospital_id"]),
        }
        started[case["id"]] = sas_datetime(case["started_at"])

    def with_times(row: dict, **times) -> dict:
        for name, iso in times.items():
            row[f"{name.upper()}_AT"] = iso
            row[f"{name.upper()}_DT"] = sas_datetime(iso)
        return row

    case_rows = []
    for case in cases:
        assessment = case["current_assessment"] or {}
        ack = case["acknowledgment"]
        case_rows.append(with_times({
            **keys[case["id"]],
            "EMS_UNIT": case["ems_unit"],
            "STATUS": case["status"],
            "OPERATIONAL_STATUS": case["operational_status"],
            "PROCESSING_STATUS": case["processing"]["status"],
            "NEEDS_REVIEW": _bool(case["processing"]["needs_review"]),
            "PREPARATION_CATEGORY": case["preparation_category"],
            "MEANINGFUL_CHANGE": assessment.get("meaningful_change"),
            "ASSESSMENT_OUTDATED": _bool(case["assessment_is_outdated"]),
            "INFO_VERSION": case["info_version"],
            "ACKNOWLEDGED_VERSION": ack["info_version"] if ack else None,
            "LATEST_ACKNOWLEDGED": _bool(case["latest_update_acknowledged"]),
            "SEGMENT_COUNT": case["segment_count"],
            "FAILED_SEGMENT_COUNT": case["failed_segment_count"],
        }, started=case["started_at"], eta=case["eta_at"], arrived=case["arrived_at"], closed=case["closed_at"]))

    events = []

    def event(event_id, case_id, event_type, occurred_at, info_version=None, actor_role=None, detail=None, value=None):
        if case_id not in keys or not occurred_at:
            return
        occurred = sas_datetime(occurred_at)
        start = started.get(case_id)
        events.append(with_times({
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
    for row in query("""SELECT s.id, s.case_id, s.seq, s.status, s.error, s.duration_ms, s.info_version,
                               s.created_at, s.transcribed_at, s.failed_at, s.updated_at, s.dismissed_at,
                               u.role AS dismissed_by_role
                        FROM transcript_segments s LEFT JOIN users u ON u.id = s.dismissed_by"""):
        event(f"segment:{row['id']}:uploaded", row["case_id"], "segment_uploaded", row["created_at"],
              actor_role="emt", detail=f"seq {row['seq']}", value=row["duration_ms"])
        if row["status"] == "completed":
            event(f"segment:{row['id']}:transcribed", row["case_id"], "segment_transcribed", row["transcribed_at"],
                  row["info_version"], detail=f"seq {row['seq']}")
        # failed_at survives a later retry; before it existed, a failed row's updated_at was the failure time.
        failed_at = row["failed_at"] or (row["updated_at"] if row["status"] == "failed" and not row["dismissed_at"] else None)
        if failed_at:
            event(f"segment:{row['id']}:failed", row["case_id"], "segment_failed", failed_at, detail=row["error"])
        event(f"segment:{row['id']}:dismissed", row["case_id"], "segment_dismissed", row["dismissed_at"],
              actor_role=row["dismissed_by_role"], detail=f"seq {row['seq']}")
    for row in query("""SELECT id, case_id, based_on_version, status, preparation_category, error,
                               latency_ms, completed_at FROM risk_assessments WHERE completed_at IS NOT NULL"""):
        event(f"assessment:{row['id']}", row["case_id"], f"assessment_{row['status']}", row["completed_at"],
              row["based_on_version"], detail=row["preparation_category"] or row["error"], value=row["latency_ms"])
    for row in query("""SELECT a.id, a.case_id, a.info_version, a.acknowledged_at, u.role
                        FROM case_acknowledgments a JOIN users u ON u.id = a.user_id"""):
        event(f"ack:{row['id']}", row["case_id"], "acknowledged", row["acknowledged_at"], row["info_version"], row["role"])
    for row in query("SELECT id, case_id, sender_role, created_at FROM messages WHERE case_id IS NOT NULL"):
        event(f"message:{row['id']}", row["case_id"], "message_sent", row["created_at"], actor_role=row["sender_role"])
    events.sort(key=lambda e: (e["OCCURRED_DT"] if e["OCCURRED_DT"] is not None else 0, e["EVENT_ID"]))

    assessment_rows = [with_times({
        "ASSESSMENT_ID": row["id"],
        **keys[row["case_id"]],
        "BASED_ON_VERSION": row["based_on_version"],
        "BASELINE_VERSION": row["baseline_version"],
        "STATUS": row["status"],
        "PREPARATION_CATEGORY": row["preparation_category"],
        "MEANINGFUL_CHANGE": row["meaningful_change"],
        "REVIEW_REASON": row["review_reason"],
        "ERROR": row["error"],
        "SCORER_VERSION": row["scorer_version"],
        "INPUT_TOKENS": row["input_tokens"],
        "OUTPUT_TOKENS": row["output_tokens"],
        "LATENCY_MS": row["latency_ms"],
        "COST_USD": row["cost_usd"],
    }, started=row["started_at"], completed=row["completed_at"])
        for row in query("SELECT * FROM risk_assessments ORDER BY id") if row["case_id"] in keys]

    vital_rows = []
    for row in query("SELECT id, case_id, update_id, name, value, measured_at FROM vital_readings ORDER BY id"):
        if row["case_id"] not in keys:
            continue
        label, unit, _, _ = VITAL_SIGNS.get(row["name"], (row["name"], "", 0, 0))
        vital_rows.append(with_times({
            "READING_ID": row["id"], **keys[row["case_id"]], "UPDATE_ID": row["update_id"],
            "NAME": row["name"], "LABEL": label, "UNIT": unit, "VALUE": row["value"],
        }, measured=row["measured_at"]))

    return {"CASES": case_rows, "EVENTS": events, "ASSESSMENTS": assessment_rows, "VITALS": vital_rows}


def table_vars(name: str) -> list:
    """CAS import options for each column, in file order: fixed types so empty columns stay numeric."""
    return [{"name": column, "type": "double" if kind == "num" else "varchar", "label": label}
            for column, kind, label in TABLES[name]]


def table_formats(name: str) -> list:
    return [{"name": column, "format": DATETIME_FORMAT} for column, _, _ in TABLES[name] if column.endswith("_DT")]


def schema(tables: dict, prefix: str) -> dict:
    """What sas/load_csvs.py needs to load the CSV files exactly as the sync would."""
    return {"tables": {prefix + name: {"rows": len(rows), "vars": table_vars(name), "formats": table_formats(name)}
                       for name, rows in tables.items()}}


def to_csv(name: str, rows: list) -> bytes:
    """CSV with the table's columns in order; missing values are empty."""
    columns = [column for column, _, _ in TABLES[name]]
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
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport)
        self._token = None
        self._token_expires = 0.0

    @property
    def cas_url(self) -> str:
        return f"{self.settings.url}/{self.settings.cas_server}-http"

    async def close(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict:
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

    async def upload_csv(self, session: str, table: str, name: str, data: bytes) -> dict:
        """table.upload into a session-scope table, with every column's type and label fixed."""
        params = {
            "casOut": {"caslib": self.settings.caslib, "name": table, "replace": True},
            "importOptions": {"fileType": "CSV", "vars": table_vars(name)},
        }
        response = await self._call(
            "PUT", f"{self.cas_url}/cas/sessions/{session}/actions/table.upload", f"upload {table}",
            headers={"Content-Type": "text/plain", "JSON-Parameters": json.dumps(params)}, content=data,
        )
        return self._check(response, f"upload {table}")

    async def replace_tables(self, tables: dict) -> None:
        """
        Replace each shared table with new contents ({name: csv bytes}; empty bytes drops it).
        Upload to a staging table, then swap it in, so a failed upload leaves the old table.
        """
        caslib = self.settings.caslib
        session = await self.open_session()
        try:
            for name, data in tables.items():
                target = self.settings.prefix + name
                if not data:
                    await self.action(session, "table.dropTable", {"caslib": caslib, "name": target, "quiet": True})
                    continue
                stage = f"{target}_STAGE"
                await self.upload_csv(session, stage, name, data)
                await self.action(session, "table.alterTable",
                                  {"caslib": caslib, "name": stage, "columns": table_formats(name)})
                await self.action(session, "table.dropTable", {"caslib": caslib, "name": target, "quiet": True})
                await self.action(session, "table.promote", {
                    "caslib": caslib, "name": stage, "target": target, "targetLib": caslib, "drop": True,
                })
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
        payloads = {name: to_csv(name, rows) if rows else b"" for name, rows in tables.items()}
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
            print(f"🔄 SAS sync on: every {self.settings.interval:g}s to {self.settings.caslib} on {self.settings.url}")
        elif self.settings.enabled:
            print("⚠️  SAS_SYNC_ENABLED is set but SAS_VIYA_URL / SAS_CLIENT_ID / SAS_CLIENT_SECRET are missing")

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
        return lines
    finally:
        await client.close()


def main(argv: list) -> int:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
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
        print("Set SAS_VIYA_URL, SAS_CLIENT_ID and SAS_CLIENT_SECRET in .env")
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
    sys.exit(main(sys.argv))
