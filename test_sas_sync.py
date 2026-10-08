"""Push to SAS Viya (sas_sync.py), against a fake SAS server: no network, no real credentials."""
import asyncio
import csv
import io
import json
import os
import wave
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import case_assessment
import database
import sas_sync
from database import init_database, insert_sample_data, query, run
from middleware.auth import create_access_token
from routes import cases, recordings

client = TestClient(app, raise_server_exceptions=False)

SETTINGS = sas_sync.Settings(url="https://viya.test", client_id="app.test", client_secret="s3cret",
                             interval=10, enabled=True)
SCORED = {"summary": "s", "meaningful_change": "No earlier report", "change_explanation": "-",
          "missing_information": "None identified", "preparation_category": "Prepare now"}


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("sas-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(scope="module", autouse=True)
def fakes(temp_database, tmp_path_factory):
    """Module-wide, so the module-scoped story below also never reaches OpenAI."""
    async def fake_score(inputs):
        return SCORED, {"input_tokens": 1000, "output_tokens": 200, "latency_ms": 900}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(recordings, "openai_client", None)
        patch.setattr(cases, "SEGMENT_DIR", tmp_path_factory.mktemp("segments"))
        patch.setattr(cases, "RETRY_DELAY_SECONDS", 0)
        patch.setattr(case_assessment, "SETTLE_SECONDS", 0)
        patch.setattr(case_assessment, "score", fake_score)
        yield


def auth(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def gen_id():
    return query("SELECT id FROM hospitals WHERE code = 'GEN'")[0]["id"]


@pytest.fixture(scope="module")
def story():
    """One case with every kind of event, made through the API."""
    run("UPDATE cases SET status = 'closed' WHERE status = 'active'")
    emt, doctor = auth("emt.wilson"), auth("dr.smith")
    case = client.post("/api/cases", json={"patient_info": "67F short of breath, lives alone",
                                            "destination_hospital_id": gen_id(), "ems_unit": "Medic 12",
                                            "eta_minutes": 15, "source_case_id": "SYN002",
                                            "source_run_id": "run1"}, headers=emt).json()
    client.post(f"/api/cases/{case['id']}/updates",
                json={"kind": "vitals", "vitals": {"spo2": 89, "hr": 126}, "body": "on room air"}, headers=emt)
    client.post(f"/api/cases/{case['id']}/acknowledge", headers=doctor)
    client.post(f"/api/cases/{case['id']}/messages", json={"body": "Bay 2 is ready for you"}, headers=doctor)

    def broken(path, prompt):
        raise openai.BadRequestError("bad audio", response=SimpleNamespace(request=None, status_code=400, headers={}),
                                     body=None)
    original = cases.transcribe_segment_sync
    cases.transcribe_segment_sync = broken
    try:
        segment = client.post(f"/api/cases/{case['id']}/segments", files={"audio": ("s.wav", b"x", "audio/wav")},
                              data={"seq": "0", "client_id": "c0"}, headers=emt).json()
    finally:
        cases.transcribe_segment_sync = original
    client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/dismiss", headers=emt)
    client.post(f"/api/cases/{case['id']}/arrive", headers=doctor)
    client.post(f"/api/cases/{case['id']}/close", headers=emt)
    return case


def rows_of(table, case_id):
    return [r for r in sas_sync.build_tables()[table] if r["CASE_ID"] == case_id]


class TestTables:
    def test_events_cover_the_whole_story_in_time_order(self, story):
        events = rows_of("EVENTS", story["id"])
        types = [e["EVENT_TYPE"] for e in events]
        for expected in ("case_started", "update_added", "assessment_completed", "acknowledged", "message_sent",
                         "segment_uploaded", "segment_failed", "segment_dismissed", "case_arrived", "case_closed"):
            assert expected in types, expected
        assert types[0] == "case_started" and types[-1] == "case_closed"
        assert [e["OCCURRED_DT"] for e in events] == sorted(e["OCCURRED_DT"] for e in events)
        assert all(e["SECONDS_SINCE_START"] >= 0 for e in events)
        assert len({e["EVENT_ID"] for e in events}) == len(events)
        assert all(e["SOURCE_CASE_ID"] == "SYN002" and e["SOURCE_RUN_ID"] == "run1" and e["HOSPITAL_CODE"] == "GEN"
                   for e in events)
        ack = next(e for e in events if e["EVENT_TYPE"] == "acknowledged")
        assert ack["ACTOR_ROLE"] == "doctor" and ack["INFO_VERSION"] == 2
        failed = next(e for e in events if e["EVENT_TYPE"] == "segment_failed")
        dismissed = next(e for e in events if e["EVENT_TYPE"] == "segment_dismissed")
        assert failed["DETAIL"] == "unsupported or corrupt audio" and failed["OCCURRED_DT"] <= dismissed["OCCURRED_DT"]

    def test_case_assessment_and_vital_rows(self, story):
        [case] = rows_of("CASES", story["id"])
        assert case["OPERATIONAL_STATUS"] == "closed" and case["PREPARATION_CATEGORY"] == "Prepare now"
        assert case["FAILED_SEGMENT_COUNT"] == 0 and case["NEEDS_REVIEW"] in (0, 1)
        assert case["ARRIVED_DT"] is not None and case["CLOSED_DT"] >= case["ARRIVED_DT"]
        assessments = rows_of("ASSESSMENTS", story["id"])
        assert assessments and assessments[-1]["INPUT_TOKENS"] == 1000 and assessments[-1]["COST_USD"] is not None
        vitals = rows_of("VITALS", story["id"])
        assert {(v["NAME"], v["VALUE"], v["UNIT"]) for v in vitals} == {("spo2", 89.0, "%"), ("hr", 126.0, "bpm")}

    def test_no_free_text_leaves_the_app(self, story):
        exported = b"".join(sas_sync.to_csv(name, rows) for name, rows in sas_sync.build_tables().items())
        for private in (b"lives alone", b"on room air", b"Bay 2 is ready"):
            assert private not in exported, private

    def test_csv_has_the_declared_columns_in_order(self, story):
        for name, rows in sas_sync.build_tables().items():
            reader = csv.reader(io.StringIO(sas_sync.to_csv(name, rows).decode()))
            header = next(reader)
            assert header == [column for column, _, _ in sas_sync.TABLES[name]]
            assert all(len(line) == len(header) for line in reader)

    def test_sas_datetime(self):
        assert sas_sync.sas_datetime("1960-01-01T00:00:00.000Z") == 0
        assert sas_sync.sas_datetime("1960-01-02T00:00:01.500Z") == 86401.5
        assert sas_sync.sas_datetime("2026-10-08T00:00:00Z") == 2_107_036_800
        assert sas_sync.sas_datetime(None) is None and sas_sync.sas_datetime("not a date") is None

    def test_every_datetime_column_is_numeric_and_paired_with_iso_text(self):
        for name, columns in sas_sync.TABLES.items():
            kinds = {column: kind for column, kind, _ in columns}
            for column in kinds:
                if column.endswith("_DT"):
                    assert kinds[column] == "num" and kinds[column[:-3] + "_AT"] == "char"


class FakeSas:
    """Records requests and answers like Viya; `fail` maps an action name to an error disposition."""

    def __init__(self, fail=None, http_error=None):
        self.requests = []
        self.fail = fail or {}
        self.http_error = http_error or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(request)
        for marker, status in self.http_error.items():
            if marker in path:
                return httpx.Response(status, json={"error": "HTTP/1.1 403 Forbidden", "disposition": None})
        if path == "/SASLogon/oauth/token":
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 29999999})
        if path.endswith("/cas/sessions") and request.method == "POST":
            return httpx.Response(200, json={"session": "sess-1"})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        action = path.rsplit("/", 1)[-1]
        if action in self.fail:
            return httpx.Response(200, json={"disposition": {"severity": "Error", "statusCode": 2710,
                                                             "formattedStatus": self.fail[action]}})
        return httpx.Response(200, json={"disposition": {"severity": "Normal", "statusCode": 0}, "results": {}})

    def calls(self):
        return [(r.method, r.url.path.replace("/cas-shared-default-http", "")) for r in self.requests]


def cas_client(fake):
    return sas_sync.CasClient(SETTINGS, transport=httpx.MockTransport(fake))


def run_async(coroutine):
    return asyncio.run(coroutine)


class TestCasClient:
    def test_replace_uploads_to_staging_then_swaps_it_in_and_closes_the_session(self):
        fake = FakeSas()
        data = sas_sync.to_csv("VITALS", [])

        async def go():
            c = cas_client(fake)
            try:
                await c.replace_tables({"VITALS": data + b"1,2,SYN1,run,GEN,3,spo2,SpO2,%,91,x,0\n"})
            finally:
                await c.close()
        run_async(go())
        assert fake.calls() == [
            ("POST", "/SASLogon/oauth/token"),
            ("POST", "/cas/sessions"),
            ("PUT", "/cas/sessions/sess-1/actions/table.upload"),
            ("POST", "/cas/sessions/sess-1/actions/table.alterTable"),
            ("POST", "/cas/sessions/sess-1/actions/table.dropTable"),
            ("POST", "/cas/sessions/sess-1/actions/table.promote"),
            ("POST", "/cas/sessions/sess-1/actions/table.save"),
            ("DELETE", "/cas/sessions/sess-1"),
        ]
        token, _, upload, alter, drop, promote, save, _ = fake.requests
        assert token.headers["authorization"].startswith("Basic ")
        assert upload.headers["authorization"] == "Bearer tok-123"
        params = json.loads(upload.headers["json-parameters"])
        assert params["casOut"] == {"caslib": "Public", "name": "ASC_VITALS_STAGE", "replace": True}
        assert params["importOptions"]["fileType"] == "CSV"
        assert [v["name"] for v in params["importOptions"]["vars"]] == [c for c, _, _ in sas_sync.TABLES["VITALS"]]
        assert {v["name"]: v["type"] for v in params["importOptions"]["vars"]}["MEASURED_DT"] == "double"
        assert json.loads(alter.content)["columns"] == [{"name": "MEASURED_DT", "format": "DATETIME22.3"}]
        assert json.loads(drop.content) == {"caslib": "Public", "name": "ASC_VITALS", "quiet": True}
        assert json.loads(promote.content) == {"caslib": "Public", "name": "ASC_VITALS_STAGE", "target": "ASC_VITALS",
                                               "targetLib": "Public", "drop": True}
        assert json.loads(save.content)["name"] == "ASC_VITALS.sashdat"

    def test_empty_table_is_dropped_not_uploaded(self):
        fake = FakeSas()

        async def go():
            c = cas_client(fake)
            try:
                await c.replace_tables({"VITALS": b""})
            finally:
                await c.close()
        run_async(go())
        assert [p for _, p in fake.calls()][2:] == ["/cas/sessions/sess-1/actions/table.dropTable", "/cas/sessions/sess-1"]

    def test_failed_action_raises_and_still_closes_the_session(self):
        fake = FakeSas(fail={"table.promote": "ERROR: Insufficient authorization"})

        async def go():
            c = cas_client(fake)
            try:
                await c.replace_tables({"CASES": b"x\n1\n"})
            finally:
                await c.close()
        with pytest.raises(sas_sync.SasError, match="table.promote: ERROR: Insufficient authorization"):
            run_async(go())
        assert fake.calls()[-1] == ("DELETE", "/cas/sessions/sess-1")

    def test_http_errors_name_the_step_and_never_include_credentials(self):
        fake = FakeSas(http_error={"table.upload": 403})

        async def go():
            c = cas_client(fake)
            try:
                await c.replace_tables({"CASES": b"x\n1\n"})
            finally:
                await c.close()
        with pytest.raises(sas_sync.SasError) as caught:
            run_async(go())
        assert str(caught.value) == "upload ASC_CASES_STAGE: HTTP 403"
        assert "tok-123" not in str(caught.value) and "s3cret" not in str(caught.value)

    def test_token_is_reused(self):
        fake = FakeSas()

        async def go():
            c = cas_client(fake)
            try:
                await c.replace_tables({"VITALS": b""})
                await c.replace_tables({"VITALS": b""})
            finally:
                await c.close()
        run_async(go())
        assert [p for _, p in fake.calls()].count("/SASLogon/oauth/token") == 1


class TestSync:
    def test_only_changed_tables_are_pushed(self, story):
        fake = FakeSas()

        async def go():
            syncer = sas_sync.SasSync(SETTINGS, client=cas_client(fake))
            try:
                first = await syncer.push()
                second = await syncer.push()
                run("UPDATE cases SET ems_unit = 'Medic 99' WHERE id = ?", (story["id"],))
                third = await syncer.push()
                return first, second, third, syncer.describe()
            finally:
                await syncer.stop()
        first, second, third, status = run_async(go())
        assert set(first) == {"CASES", "EVENTS", "ASSESSMENTS", "VITALS"}
        assert second == [] and third == ["CASES"]
        assert status["last_error"] is None and status["tables"]["ASC_CASES"]["rows"] >= 1
        assert "client_secret" not in json.dumps(status) and "s3cret" not in json.dumps(status)

    def test_failures_back_off_and_recover(self, story):
        syncer = sas_sync.SasSync(SETTINGS, client=cas_client(FakeSas(http_error={"/cas/sessions": 403})))
        syncer._failures = 1
        assert syncer.next_delay() == 20
        syncer._failures = 10
        assert syncer.next_delay() == sas_sync.MAX_BACKOFF_SECONDS
        syncer._failures = 0
        assert syncer.next_delay() == 10

    def test_run_forever_records_the_error_and_keeps_going(self, story):
        fake = FakeSas(http_error={"/cas/sessions": 403})
        syncer = sas_sync.SasSync(SETTINGS, client=cas_client(fake))
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise asyncio.CancelledError
        syncer.sleep = fake_sleep

        async def go():
            try:
                await syncer.run_forever()
            except asyncio.CancelledError:
                pass
            finally:
                await syncer.stop()
        run_async(go())
        assert sleeps == [20, 40]
        assert syncer.status["last_error"] == "open CAS session: HTTP 403"
        assert syncer.describe()["consecutive_failures"] == 2

    def test_disabled_or_unconfigured_never_starts(self):
        async def go():
            for settings in (sas_sync.Settings(url="", client_id="", client_secret="", enabled=True),
                             sas_sync.Settings(url="https://v", client_id="a", client_secret="b", enabled=False)):
                syncer = sas_sync.SasSync(settings)
                syncer.start()
                assert syncer.describe()["running"] is False
        run_async(go())


class TestStatusRoute:
    def test_hospital_users_only(self):
        assert client.get("/api/sas/status").status_code == 401
        assert client.get("/api/sas/status", headers=auth("emt.wilson")).status_code == 403
        status = client.get("/api/sas/status", headers=auth("dr.smith")).json()
        assert {"enabled", "configured", "running", "last_success_at", "last_error", "tables"} <= set(status)
        assert "client_secret" not in json.dumps(status)

    def test_check_reports_sign_in_problems(self, monkeypatch):
        async def failing_check(settings):
            raise sas_sync.SasError("sign-in failed: HTTP 401")
        monkeypatch.setattr(sas_sync, "check", failing_check)
        monkeypatch.setattr(sas_sync.sync, "settings", SETTINGS)
        status = client.get("/api/sas/status?check=true", headers=auth("dr.smith")).json()
        assert status["check"] == {"ok": False, "error": "sign-in failed: HTTP 401"}


class TestCsvAndLoader:
    def test_csv_command_writes_tables_and_schema(self, story, tmp_path):
        assert sas_sync.main(["sas_sync", "csv", str(tmp_path)]) == 0
        schema = json.loads((tmp_path / "schema.json").read_text())
        assert set(schema["tables"]) == {"ASC_CASES", "ASC_EVENTS", "ASC_ASSESSMENTS", "ASC_VITALS"}
        for table, spec in schema["tables"].items():
            name = table[len("ASC_"):]
            header = (tmp_path / f"{table}.csv").read_text().splitlines()[0].split(",")
            assert [v["name"] for v in spec["vars"]] == header
            assert spec["vars"] == sas_sync.table_vars(name) and spec["formats"] == sas_sync.table_formats(name)

    def test_loader_runs_the_same_steps_as_the_push(self, story, tmp_path):
        from sas import load_csvs
        sas_sync.main(["sas_sync", "csv", str(tmp_path)])
        schema = json.loads((tmp_path / "schema.json").read_text())
        schema["tables"]["ASC_VITALS"]["rows"] = 0  # an empty table is skipped, not uploaded
        (tmp_path / "schema.json").write_text(json.dumps(schema))
        calls = []

        class Table:
            def __getattr__(self, action):
                def call(**params):
                    calls.append((action, params))
                    return SimpleNamespace(severity=0)
                return call

        conn = SimpleNamespace(table=Table(), upload_file=lambda path, **params: calls.append(("upload", params)))
        load_csvs.load_tables(conn, str(tmp_path))
        actions = [name for name, _ in calls]
        assert actions == ["upload", "alterTable", "dropTable", "promote", "save"] * 3
        upload = calls[0][1]
        assert upload["casout"] == {"name": "ASC_CASES_STAGE", "caslib": "Public", "replace": True}
        assert upload["importoptions"]["vars"] == sas_sync.table_vars("CASES")
        assert calls[3][1] == {"name": "ASC_CASES_STAGE", "caslib": "Public", "target": "ASC_CASES",
                               "targetLib": "Public", "drop": True}

    def test_loader_stops_on_a_failed_action(self, story, tmp_path):
        from sas import load_csvs
        sas_sync.main(["sas_sync", "csv", str(tmp_path)])

        class Table:
            def __getattr__(self, action):
                return lambda **params: SimpleNamespace(severity=2 if action == "promote" else 0,
                                                        status="Insufficient authorization")
        conn = SimpleNamespace(table=Table(), upload_file=lambda path, **params: None)
        with pytest.raises(RuntimeError, match="publish ASC_CASES failed: Insufficient authorization"):
            load_csvs.load_tables(conn, str(tmp_path))
