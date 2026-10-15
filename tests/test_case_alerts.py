"""Crew alerts (case_alerts.py, routes/alerts.py): called by the crew, acknowledged by the hospital."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import case_assessment
import case_extraction
import database
from database import init_database, insert_sample_data, query, run
from middleware.auth import create_access_token

client = TestClient(app, raise_server_exceptions=False)

EMT, OTHER_EMT = "emt.wilson", "emt.garcia"
DOCTOR, OTHER_DOCTOR = "dr.smith", "dr.jones"   # General, Northside


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("alerts-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    """The AI steps never call a model here; the assessment records what it was given."""
    calls = []

    async def fake_score(inputs):
        calls.append(inputs["input"])
        return {"summary": "s", "meaningful_change": "No earlier report", "change_explanation": "-",
                "missing_information": "None identified", "preparation_category": "Can wait"}, {}

    async def fake_extract(info):
        return {"chief_complaint": [], "vitals": [], "findings": [], "treatments": []}, {}

    monkeypatch.setattr(case_assessment, "score", fake_score)
    monkeypatch.setattr(case_extraction, "extract", fake_extract)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)
    run("UPDATE cases SET status = 'closed' WHERE status = 'active'")
    return calls


def auth(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def open_case(emt=EMT, code="GEN"):
    hospital = query("SELECT id FROM hospitals WHERE code = ?", (code,))[0]["id"]
    response = client.post("/api/cases", json={"patient_info": "70M sudden left-sided weakness",
                                               "destination_hospital_id": hospital, "eta_minutes": 12},
                           headers=auth(emt))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def call(case_id, username=EMT, **body):
    return client.post(f"/api/cases/{case_id}/alerts", json={"action": "called", **body}, headers=auth(username))


def case_as(case_id, username=DOCTOR):
    response = client.get(f"/api/cases/{case_id}", headers=auth(username))
    assert response.status_code == 200, response.text
    return response.json()


def test_a_called_alert_reaches_the_hospital_with_its_key_time(no_models):
    case_id = open_case()
    before = case_as(case_id)
    response = call(case_id, alert="stroke", key_time=ago(40))
    assert response.status_code == 201, response.text
    assert response.json()["key_time_label"] == "Last known well"

    after = case_as(case_id)
    alerts = after["crew_alerts"]
    [stroke] = alerts["active"]
    assert (stroke["alert"], stroke["label"], stroke["author_name"]) == ("stroke", "Stroke", "Mike Wilson")
    assert alerts["unacknowledged"] is True and alerts["acknowledgment"] is None
    # The alert is the crew's call, kept apart from the reports: the information version, the
    # acknowledgment of it and the AI's input are exactly as before.
    assert after["info_version"] == before["info_version"]
    assert after["updated_at"] > before["updated_at"]
    assert case_assessment.read_inputs(case_id)["input"] == no_models[-1]
    assert "stroke" not in str(case_assessment.read_inputs(case_id)["input"]).lower()
    # It is in the queue's copy of the case too.
    queued = client.get("/api/cases/queue", headers=auth(DOCTOR)).json()["cases"]
    assert next(c for c in queued if c["id"] == case_id)["crew_alerts"]["active"][0]["alert"] == "stroke"


def test_an_unstated_key_time_stays_unstated():
    case_id = open_case()
    assert call(case_id, alert="sepsis").status_code == 201
    [sepsis] = case_as(case_id)["crew_alerts"]["active"]
    assert sepsis["key_time"] is None and sepsis["key_time_label"] == "Symptom onset"


def test_each_change_needs_acknowledging_again():
    case_id = open_case()
    first = call(case_id, alert="stroke", key_time=ago(50)).json()
    ack = client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(DOCTOR))
    assert ack.status_code == 200, ack.text
    alerts = ack.json()["crew_alerts"]
    assert alerts["unacknowledged"] is False
    assert (alerts["acknowledgment"]["through_alert_id"], alerts["acknowledgment"]["user_name"]) == \
        (first["id"], "John Smith")
    # The crew sees who acknowledged it.
    assert case_as(case_id, EMT)["crew_alerts"]["acknowledgment"]["user_name"] == "John Smith"

    # A corrected last-known-well time is a change to acknowledge; the alert stays active once.
    corrected = call(case_id, alert="stroke", key_time=ago(70)).json()
    alerts = case_as(case_id)["crew_alerts"]
    assert alerts["unacknowledged"] is True
    assert [a["id"] for a in alerts["active"]] == [corrected["id"]]

    # So is a cancellation (the team can stand down).
    client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(DOCTOR))
    cancelled = client.post(f"/api/cases/{case_id}/alerts", json={"alert": "stroke", "action": "cancelled"},
                            headers=auth(EMT))
    assert cancelled.status_code == 201, cancelled.text
    alerts = case_as(case_id)["crew_alerts"]
    assert alerts["active"] == [] and alerts["unacknowledged"] is True
    history = client.get(f"/api/cases/{case_id}/alerts", headers=auth(DOCTOR)).json()
    assert [(c["alert"], c["action"]) for c in history] == [("stroke", "called")] * 2 + [("stroke", "cancelled")]


def test_acknowledging_what_was_on_screen_leaves_a_later_change_unacknowledged():
    case_id = open_case()
    seen = call(case_id, alert="stemi", key_time=ago(30)).json()
    call(case_id, alert="trauma", key_time=ago(20))
    response = client.post(f"/api/cases/{case_id}/alerts/acknowledge", json={"through_alert_id": seen["id"]},
                           headers=auth(DOCTOR))
    assert response.status_code == 200
    alerts = response.json()["crew_alerts"]
    assert alerts["unacknowledged"] is True
    assert [a["alert"] for a in alerts["active"]] == ["stemi", "trauma"]


def test_retries_and_repeated_calls_are_stored_once():
    case_id = open_case()
    key_time = ago(10)  # a retry resends the identical body
    first = call(case_id, alert="stroke", key_time=key_time, client_id="tap-1")
    retry = call(case_id, alert="stroke", key_time=key_time, client_id="tap-1")
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["id"] == first.json()["id"]
    same = call(case_id, alert="stroke", key_time=first.json()["key_time"])
    assert same.status_code == 200 and same.json()["id"] == first.json()["id"]
    assert len(client.get(f"/api/cases/{case_id}/alerts", headers=auth(EMT)).json()) == 1


def test_a_used_client_id_cannot_carry_a_different_change():
    """Astra's Oct 13 audit (R3): a call commits, its reply is lost, and the crew cancels. Reusing the call's
    client_id must not return the old call as if the cancellation had worked."""
    case_id = open_case()
    assert call(case_id, alert="stroke", client_id="lost-reply").status_code == 201
    cancel = call(case_id, alert="stroke", action="cancelled", client_id="lost-reply")
    assert cancel.status_code == 409, cancel.json()
    assert call(case_id, alert="stroke", key_time=ago(5), client_id="lost-reply").status_code == 409
    assert call(case_id, alert="stroke", client_id="lost-reply").status_code == 200  # the same change, retried
    assert case_as(case_id)["crew_alerts"]["active"][0]["alert"] == "stroke"


@pytest.mark.parametrize("body, status", [
    ({"alert": "cardiac arrest"}, 400),
    ({"alert": "stroke", "action": "paged"}, 400),
    ({"alert": "stroke", "key_time": "yesterday"}, 400),
    ({"alert": "stroke", "key_time": "2999-01-01T00:00:00Z"}, 400),
    ({"alert": "stroke", "key_time": "2020-01-01T00:00:00Z"}, 400),
    ({"alert": "stroke", "client_id": ""}, 400),
    ({"alert": "stroke", "action": "cancelled"}, 409),   # not active
])
def test_invalid_alerts_are_rejected(body, status):
    case_id = open_case()
    response = client.post(f"/api/cases/{case_id}/alerts", json={"action": "called", **body}, headers=auth(EMT))
    assert response.status_code == status, response.text
    assert case_as(case_id)["crew_alerts"]["latest_change_id"] is None


def test_only_the_crew_calls_and_only_the_receiving_hospital_acknowledges():
    case_id = open_case()
    assert call(case_id, OTHER_EMT, alert="stroke").status_code == 404
    assert call(case_id, DOCTOR, alert="stroke").status_code == 403
    assert client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(DOCTOR)).status_code == 409
    call(case_id, alert="stroke")
    assert client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(EMT)).status_code == 403
    assert client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(OTHER_DOCTOR)).status_code == 404
    assert client.get(f"/api/cases/{case_id}/alerts", headers=auth(OTHER_DOCTOR)).status_code == 404
    assert client.post(f"/api/cases/{case_id}/alerts/acknowledge", json={"through_alert_id": 999999},
                       headers=auth(DOCTOR)).status_code == 400


def test_a_closed_case_takes_no_new_alerts():
    case_id = open_case()
    client.post(f"/api/cases/{case_id}/close", headers=auth(EMT))
    assert call(case_id, alert="trauma").status_code == 409


def test_alerts_and_the_queue_moves_they_cause_are_in_the_sas_export():
    """Astra's Oct 13 audit (R6): under draft-4 an alert reorders the queue, so the export must show the
    alert, its key time and its acknowledgment, and record it as the cause of the move."""
    import sas_sync
    first = open_case()
    case_id = open_case(emt="emt.lee")  # second in the queue until its crew calls an alert
    key_time = ago(20)
    assert call(case_id, "emt.lee", alert="stroke", key_time=key_time, client_id="sas-1").status_code == 201
    assert client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(DOCTOR)).status_code == 200
    tables = sas_sync.build_tables()
    events = [e for e in tables["EVENTS"] if e["CASE_ID"] == case_id and e["EVENT_TYPE"].startswith("alert_")]
    assert [(e["EVENT_TYPE"], e["DETAIL"]) for e in sorted(events, key=lambda e: e["EVENT_ID"])] == [
        ("alert_called", "stroke"), ("alert_key_time", "stroke"), ("alert_acknowledged", "stroke")]
    [key_event] = [e for e in events if e["EVENT_TYPE"] == "alert_key_time"]
    assert key_event["SECONDS_SINCE_START"] < 0  # last known well was before the case opened
    moves = [row for row in tables["QUEUE"] if row["CAUSE"] == "alert_called"]
    assert {row["CASE_ID"] for row in moves} == {case_id, first}  # the alert moved both patients
    assert all(row["CAUSE_CASE_ID"] == case_id for row in moves)
