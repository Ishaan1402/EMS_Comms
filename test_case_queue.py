"""The hospital inbound queue (case_queue.py, GET /api/cases/queue)."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import case_assessment
import database
from database import init_database, insert_sample_data, query, run
from middleware.auth import create_access_token
from routes import recordings

client = TestClient(app, raise_server_exceptions=False)

DOCTOR = "dr.smith"          # General Hospital
OTHER_DOCTOR = "dr.jones"    # Northside
EMTS = ["emt.wilson", "emt.garcia", "emt.lee"]


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("queue-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(recordings, "openai_client", None)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)
    run("UPDATE cases SET status = 'closed' WHERE status = 'active'")


def auth(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def hospital(code):
    return query("SELECT id FROM hospitals WHERE code = ?", (code,))[0]["id"]


def score_as(monkeypatch, categories):
    """Each case's assessment returns the category listed for its patient_info."""
    async def fake_score(inputs):
        text = inputs["input"]["current_transcript"]
        category = next(c for key, c in categories.items() if key in text)
        return {"summary": "s", "meaningful_change": "No earlier report", "change_explanation": "-",
                "missing_information": "None identified", "preparation_category": category}, {}
    monkeypatch.setattr(case_assessment, "score", fake_score)


def open_case(emt, info, eta=None, code="GEN"):
    body = {"patient_info": info, "destination_hospital_id": hospital(code)}
    if eta is not None:
        body["eta_minutes"] = eta
    response = client.post("/api/cases", json=body, headers=auth(emt))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def acknowledge(case_id):
    client.post(f"/api/cases/{case_id}/acknowledge", headers=auth(DOCTOR))


def queue(username=DOCTOR):
    response = client.get("/api/cases/queue", headers=auth(username))
    assert response.status_code == 200, response.text
    return response.json()


def test_order_follows_the_draft_rule(monkeypatch):
    score_as(monkeypatch, {"routine": "Routine", "wait": "Can wait", "now": "Prepare now"})
    routine = open_case("emt.wilson", "routine patient", eta=5)
    wait = open_case("emt.garcia", "wait patient", eta=20)
    now = open_case("emt.lee", "now patient", eta=30)
    for case_id in (routine, wait, now):
        acknowledge(case_id)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [now, wait, routine]
    assert [c["queue_position"] for c in body["cases"]] == [1, 2, 3]
    assert body["cases"][0]["queue_reasons"] == ["Prepare now", "ETA 30 min"]
    assert body["rule_version"] == "draft-2"


def test_unacknowledged_information_comes_before_category(monkeypatch):
    score_as(monkeypatch, {"routine": "Routine", "now": "Prepare now"})
    now = open_case("emt.wilson", "now patient", eta=10)
    routine = open_case("emt.garcia", "routine patient", eta=10)
    acknowledge(now)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [routine, now]
    assert body["cases"][0]["queue_reasons"][0] == "New information not yet acknowledged"
    assert body["counts"] == {"inbound": 2, "needs_review": 0, "unacknowledged": 1, "prepare_now": 1}


def test_unknown_category_ranks_above_can_wait(monkeypatch):
    score_as(monkeypatch, {"wait": "Can wait", "unsure": "Unsure"})
    wait = open_case("emt.wilson", "wait patient", eta=5)
    unsure = open_case("emt.garcia", "unsure patient", eta=5)
    acknowledge(wait)
    acknowledge(unsure)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [unsure, wait]
    assert body["cases"][0]["queue_reasons"] == ["Cannot assess", "ETA 5 min"]


def test_cannot_assess_needs_review_until_acknowledged(monkeypatch):
    score_as(monkeypatch, {"now": "Prepare now", "unsure": "Unsure"})
    now = open_case("emt.wilson", "now patient", eta=30)
    unsure = open_case("emt.garcia", "unsure patient", eta=5)
    acknowledge(now)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [unsure, now]
    assert body["cases"][0]["queue_reasons"][0] == "Needs review: Insufficient information to assess preparation needs."
    assert body["counts"]["needs_review"] == 1

    # The doctor has read it: an unknown no longer outranks a known Prepare now.
    acknowledge(unsure)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [now, unsure]
    assert body["counts"]["needs_review"] == 0
    case = client.get(f"/api/cases/{unsure}", headers=auth(DOCTOR)).json()
    assert case["preparation_category"] == "Cannot assess"
    assert case["processing"]["status"] == "completed" and case["processing"]["reasons"] == []


def test_acknowledging_does_not_clear_a_processing_failure(monkeypatch):
    async def failing_score(inputs):
        raise RuntimeError("outage")
    monkeypatch.setattr(case_assessment, "score", failing_score)
    failed = open_case("emt.wilson", "any patient", eta=30)
    acknowledge(failed)
    case = queue()["cases"][0]
    assert case["id"] == failed and case["processing"]["needs_review"]
    assert case["queue_reasons"][0] == "Needs review: Assessment failed: unexpected error"


def test_sooner_eta_first_and_missing_eta_last(monkeypatch):
    score_as(monkeypatch, {"a": "Can wait", "b": "Can wait", "c": "Can wait"})
    late = open_case("emt.wilson", "a patient", eta=40)
    soon = open_case("emt.garcia", "b patient", eta=5)
    unknown = open_case("emt.lee", "c patient")
    for case_id in (late, soon, unknown):
        acknowledge(case_id)
    assert [c["id"] for c in queue()["cases"]] == [soon, late, unknown]


def test_arrived_closed_and_other_hospitals_cases_are_not_in_the_queue(monkeypatch):
    score_as(monkeypatch, {"x": "Can wait"})
    arrived = open_case("emt.wilson", "x patient", eta=5)
    client.post(f"/api/cases/{arrived}/arrive", headers=auth(DOCTOR))
    closed = open_case("emt.garcia", "x patient", eta=5)
    client.post(f"/api/cases/{closed}/close", headers=auth("emt.garcia"))
    northside = open_case("emt.lee", "x patient", eta=5, code="NORTH")
    assert queue()["cases"] == []
    assert [c["id"] for c in queue(OTHER_DOCTOR)["cases"]] == [northside]


def test_only_hospital_users_have_a_queue():
    assert client.get("/api/cases/queue", headers=auth("emt.wilson")).status_code == 403
    assert client.get("/api/cases/queue").status_code == 401


def test_rows_say_which_vitals_changed_typed_or_from_the_radio(monkeypatch):
    score_as(monkeypatch, {"x": "Can wait"})
    case_id = open_case("emt.wilson", "x patient", eta=10)
    for vitals in ({"hr": 110, "sbp": 120, "dbp": 80, "spo2": 95}, {"hr": 125, "sbp": 100, "dbp": 60, "spo2": 95}):
        response = client.post(f"/api/cases/{case_id}/updates", json={"kind": "vitals", "vitals": vitals},
                               headers=auth("emt.wilson"))
        assert response.status_code == 201, response.text
    [case] = queue()["cases"]
    assert [(v["label"], v["from"], v["to"]) for v in case["vital_changes"]] == [("HR", "110", "125"),
                                                                               ("BP", "120/80", "100/60")]

    # A heart rate extracted from a later radio report continues the same series.
    now = database.utc_now_iso()
    run_id = run("""INSERT INTO extraction_runs (case_id, from_version, to_version, status, extractor_version,
                                                 started_at, updated_at)
                    VALUES (?, 0, 3, 'completed', 'test', ?, ?)""", (case_id, now, now))["id"]
    run("""INSERT INTO case_findings (case_id, run_id, finding_type, value, vital_name, vital_value, recorded_at,
                                      created_at)
           VALUES (?, ?, 'vital', 'HR 131 bpm', 'hr', 131, ?, ?)""", (case_id, run_id, now, now))
    [case] = queue()["cases"]
    assert case["vital_changes"][0] == {"name": "hr", "label": "HR", "from": "125", "to": "131", "unit": "bpm",
                                        "from_qualifier": None, "to_qualifier": None}

    # Saturation on room air then on oxygen: the conditions are part of the change.
    for value, qualifier in ((89, "on room air"), (92, "on 4 liters oxygen")):
        run("""INSERT INTO case_findings (case_id, run_id, finding_type, value, detail, vital_name, vital_value,
                                          recorded_at, created_at)
               VALUES (?, ?, 'vital', 'SpO2', ?, 'spo2', ?, ?, ?)""",
            (case_id, run_id, qualifier, value, database.utc_now_iso(), now))
    spo2 = next(v for v in queue()["cases"][0]["vital_changes"] if v["name"] == "spo2")
    assert (spo2["from"], spo2["from_qualifier"], spo2["to"], spo2["to_qualifier"]) == \
        ("89", "on room air", "92", "on 4 liters oxygen")


def test_queue_snapshots_are_ordered_by_as_of(monkeypatch):
    score_as(monkeypatch, {"x": "Can wait"})
    open_case("emt.wilson", "x patient", eta=10)
    first, second = queue(), queue()
    assert first["as_of"] < second["as_of"]  # screens keep the later snapshot whatever order they arrive in
