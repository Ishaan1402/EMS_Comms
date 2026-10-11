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
    assert body["rule_version"] == "draft-1"


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
    # "Cannot assess" needs review, so it is first; it would rank above Can wait regardless.
    assert [c["id"] for c in body["cases"]] == [unsure, wait]
    assert body["cases"][0]["queue_reasons"][0].startswith("Needs review: ")


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
