"""Doctors' own queue settings: Auto-order on/off, their hand-set order, and custom risk fields."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from backend import case_assessment
from backend import database
from backend import openai_config
from backend.database import init_database, insert_sample_data, query
from main import app
from backend.middleware.auth import create_access_token

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "config.db")
    monkeypatch.setattr(openai_config, "openai_client", None)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)
    init_database()
    insert_sample_data()


def auth(username="dr.smith"):
    return {"Authorization": "Bearer " + create_access_token(
        query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0])}


def open_case(emt, eta, code="GEN"):
    hospital_id = query("SELECT id FROM hospitals WHERE code = ?", (code,))[0]["id"]
    response = client.post("/api/cases", headers=auth(emt), json={
        "destination_hospital_id": hospital_id, "patient_info": "Synthetic transport", "eta_minutes": eta})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def queue(username="dr.smith"):
    return client.get("/api/cases/queue", headers=auth(username)).json()


def test_auto_order_off_shows_the_doctors_order_and_keeps_the_rule_history():
    early, late = open_case("emt.wilson", 5), open_case("emt.garcia", 20)
    assert queue()["auto_order"] is True
    assert [c["id"] for c in queue()["cases"]] == [early, late]
    history = query("SELECT * FROM queue_events")

    assert client.put("/api/hospitals/mine/auto-order", headers=auth(), json={"auto_order": False}).status_code == 200
    assert client.put("/api/hospitals/mine/doctor-order", headers=auth(),
                      json={"case_ids": [late, early]}).status_code == 200
    body = queue()
    assert body["auto_order"] is False
    assert [(c["id"], c["queue_position"], c["suggested_position"]) for c in body["cases"]] == [
        (late, 1, 2), (early, 2, 1)]
    # A case the doctors haven't placed yet goes on top.
    newest = open_case("emt.lee", 30)
    assert [c["id"] for c in queue()["cases"]] == [newest, late, early]
    # Another hospital is unaffected, and queue history still follows the rule.
    assert queue("dr.jones")["auto_order"] is True
    assert query("SELECT * FROM queue_events WHERE case_id != ?", (newest,)) == history

    client.put("/api/hospitals/mine/auto-order", headers=auth(), json={"auto_order": True})
    assert [c["id"] for c in queue()["cases"]] == [early, late, newest]


@pytest.mark.parametrize("body", [{"auto_order": 1}, {}, {"auto_order": "false"}])
def test_auto_order_must_be_a_boolean(body):
    assert client.put("/api/hospitals/mine/auto-order", headers=auth(), json=body).status_code == 400


@pytest.mark.parametrize("ids", [None, [1, 1], ["1"], [True]])
def test_doctor_order_must_be_distinct_ids(ids):
    assert client.put("/api/hospitals/mine/doctor-order", headers=auth(), json={"case_ids": ids}).status_code == 400


def test_risk_fields_are_per_hospital_and_values_are_checked():
    case_id = open_case("emt.wilson", 10)
    add = lambda label, kind, user="dr.smith": client.post(  # noqa: E731
        "/api/hospitals/mine/risk-fields", headers=auth(user), json={"label": label, "kind": kind})
    anticoag = add("On blood thinners", "yes_no").json()
    score = add("Frailty score", "number").json()
    assert add("", "text").status_code == 400 and add("Notes", "colour").status_code == 400
    assert [f["label"] for f in client.get("/api/hospitals/mine/config", headers=auth()).json()["risk_fields"]] == [
        "On blood thinners", "Frailty score"]
    assert client.get("/api/hospitals/mine/config", headers=auth("dr.jones")).json()["risk_fields"] == []

    put = lambda field, value, user="dr.smith": client.put(  # noqa: E731
        f"/api/cases/{case_id}/risk-values", headers=auth(user), json={"field_id": field["id"], "value": value})
    assert put(anticoag, True).json()["values"] == {str(anticoag["id"]): True}
    assert put(score, "high").status_code == 400 and put(anticoag, 3).status_code == 400
    assert put(score, 6).json()["values"] == {str(anticoag["id"]): True, str(score["id"]): 6}
    assert put(score, None).json()["values"][str(score["id"])] is None
    # Another hospital's doctor can't see or write this case.
    assert put(anticoag, False, user="dr.jones").status_code == 404
    assert client.get(f"/api/cases/{case_id}/risk-values", headers=auth("emt.wilson")).status_code == 403

    assert client.delete(f"/api/hospitals/mine/risk-fields/{score['id']}", headers=auth("dr.jones")).status_code == 404
    assert client.delete(f"/api/hospitals/mine/risk-fields/{score['id']}", headers=auth()).status_code == 204
    assert put(score, 4).status_code == 400
