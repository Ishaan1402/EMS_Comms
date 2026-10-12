"""KAN-34: persisted, isolated hospital display settings through production routes."""
import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

import case_assessment
import database
from database import init_database, insert_sample_data, query, run
from main import app
from middleware.auth import create_access_token
from routes import cases
from realtime import broker

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "workflow.db")
    init_database()
    insert_sample_data()


def auth(username="dr.smith"):
    return {"Authorization": "Bearer " + create_access_token(
        query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0])}


def get(username="dr.smith"):
    response = client.get("/api/hospitals/mine", headers=auth(username))
    assert response.status_code == 200
    return response.json()


def save(minutes, version=1, username="dr.smith", **extra):
    return client.patch("/api/hospitals/mine", headers=auth(username), json={
        "arrival_highlight_minutes": minutes, "expected_version": version, **extra})


def test_two_hospital_settings_survive_reinitialization_and_do_not_rescore(monkeypatch):
    async def no_assessment(*args, **kwargs):
        pass
    monkeypatch.setattr(case_assessment.runner, "request", no_assessment)
    general, north = get(), get("dr.jones")
    assert general["arrival_highlight_minutes"] == north["arrival_highlight_minutes"] == 5
    assert general["settings_version"] == 1
    response = client.post("/api/cases", headers=auth("emt.wilson"), json={
        "destination_hospital_id": general["id"], "patient_info": "Synthetic transport", "eta_minutes": 12})
    assert response.status_code == 201
    before = client.get("/api/cases/queue", headers=auth()).json()
    history = query("SELECT * FROM queue_events")

    # A display change must never schedule a model call or a queue-policy transition.
    async def unexpected_assessment(*args, **kwargs):
        pytest.fail("Display settings triggered an assessment")
    monkeypatch.setattr(case_assessment.runner, "request", unexpected_assessment)
    changed = save(15).json()
    assert changed["settings_version"] == 2 and changed["settings_updated_at"]
    assert save(2, username="dr.jones").status_code == 200
    init_database()
    init_database()
    assert get()["arrival_highlight_minutes"] == 15
    assert get("dr.jones")["arrival_highlight_minutes"] == 2
    after = client.get("/api/cases/queue", headers=auth()).json()
    assert before["cases"] == after["cases"]
    assert before["rule_version"] == after["rule_version"]
    assert query("SELECT * FROM queue_events") == history


@pytest.mark.parametrize("minutes", [0, 61, -1, True, 5.5, "10", None])
def test_invalid_highlight_is_rejected(minutes):
    assert save(minutes).status_code == 400
    assert get()["settings_version"] == 1


def test_noop_and_stale_writes_preserve_the_saved_version():
    original = get()
    assert save(5).json() == original
    assert save(12).status_code == 200
    assert save(20).status_code == 409
    assert get()["arrival_highlight_minutes"] == 12
    for version in (True, "2", 0, None):
        assert save(20, version=version).status_code == 400


def test_only_own_hospital_doctors_can_edit():
    body = {"arrival_highlight_minutes": 10, "expected_version": 1}
    assert client.patch("/api/hospitals/mine", json=body).status_code == 401
    assert save(10, username="emt.wilson").status_code == 403
    assert save(10, hospital_id=get("dr.jones")["id"]).status_code == 400
    run("UPDATE users SET hospital_id = NULL WHERE username = 'dr.smith'")
    assert save(10).status_code == 403
    assert get("dr.jones")["settings_version"] == 1


def test_setting_events_only_reach_the_own_hospital():
    def subscribe(username):
        user = query("SELECT id, role FROM users WHERE username = ?", (username,))[0]
        return broker.subscribe(cases.event_filter(user))
    own, other, emt = [subscribe(name) for name in ("dr.smith", "dr.jones", "emt.wilson")]
    try:
        assert save(15).status_code == 200
        event = own.queue.get_nowait()
        assert "event: hospital.updated" in event and '"arrival_highlight_minutes":15' in event
        assert other.queue.empty() and emt.queue.empty()
        assert save(15, version=2).status_code == 200
        assert own.queue.empty()  # an idempotent save doesn't send duplicate events
    finally:
        for sub in (own, other, emt):
            broker.unsubscribe(sub)
