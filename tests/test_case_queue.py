"""The hospital inbound queue (case_queue.py, GET /api/cases/queue)."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
from backend import case_assessment
from backend import case_queue
from backend import database
from backend.database import init_database, insert_sample_data, query, run
from backend.middleware.auth import create_access_token
from backend import openai_config
from backend.routes import cases

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
    monkeypatch.setattr(openai_config, "openai_client", None)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)
    run("UPDATE cases SET status = 'closed' WHERE status = 'active'")
    run("DELETE FROM queue_events")
    run("DELETE FROM queue_state")


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


def call_alert(case_id, emt, alert):
    response = client.post(f"/api/cases/{case_id}/alerts", json={"alert": alert, "action": "called"}, headers=auth(emt))
    assert response.status_code == 201, response.text


def acknowledge_alerts(case_id):
    response = client.post(f"/api/cases/{case_id}/alerts/acknowledge", headers=auth(DOCTOR))
    assert response.status_code == 200, response.text


def queue(username=DOCTOR):
    response = client.get("/api/cases/queue", headers=auth(username))
    assert response.status_code == 200, response.text
    return response.json()


def test_the_ai_category_is_shown_but_does_not_set_the_order(monkeypatch):
    score_as(monkeypatch, {"routine": "Routine", "wait": "Can wait", "now": "Prepare now"})
    routine = open_case("emt.wilson", "routine patient", eta=5)
    wait = open_case("emt.garcia", "wait patient", eta=20)
    now = open_case("emt.lee", "now patient", eta=30)
    for case_id in (routine, wait, now):
        acknowledge(case_id)
    body = queue()
    # draft-3 put "now" first on the AI's Prepare now; draft-4 orders by arrival.
    assert [c["id"] for c in body["cases"]] == [routine, wait, now]
    assert [c["queue_position"] for c in body["cases"]] == [1, 2, 3]
    assert [c["preparation_category"] for c in body["cases"]] == ["Routine", "Can wait", "Prepare now"]
    # "Why here" names only what the rule used.
    assert body["cases"][2]["queue_reasons"] == ["ETA 30 min"]
    assert body["counts"]["prepare_now"] == 1 and body["counts"]["crew_alerts"] == 0
    assert body["rule_version"] == "draft-4"


def test_unacknowledged_information_comes_first(monkeypatch):
    score_as(monkeypatch, {"routine": "Routine", "now": "Prepare now"})
    now = open_case("emt.wilson", "now patient", eta=10)
    routine = open_case("emt.garcia", "routine patient", eta=10)
    acknowledge(now)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [routine, now]
    assert body["cases"][0]["queue_reasons"][0] == "New information not yet acknowledged"
    assert body["counts"] == {"inbound": 2, "needs_review": 0, "unacknowledged": 1, "crew_alerts": 0,
                              "prepare_now": 1}


def test_a_new_ai_category_does_not_move_anyone(monkeypatch):
    score_as(monkeypatch, {"first": "Routine", "second": "Routine"})
    first = open_case("emt.wilson", "first patient", eta=5)
    second = open_case("emt.garcia", "second patient", eta=20)
    acknowledge(first)
    acknowledge(second)
    before = len(history())
    # The AI re-assesses the later patient as Prepare now (an ETA refresh needs no acknowledgment).
    score_as(monkeypatch, {"first": "Routine", "second": "Prepare now"})
    client.post(f"/api/cases/{second}/updates", json={"kind": "eta", "eta_minutes": 19}, headers=auth("emt.garcia"))
    body = queue()
    assert body["cases"][1]["id"] == second and body["cases"][1]["preparation_category"] == "Prepare now"
    assert [c["id"] for c in body["cases"]] == [first, second]
    assert history()[before:] == []


def test_a_crew_alert_outranks_the_ai(monkeypatch):
    score_as(monkeypatch, {"stroke": "Can wait", "breathing": "Prepare now"})
    breathing = open_case("emt.wilson", "breathing patient", eta=5)
    stroke = open_case("emt.garcia", "stroke patient", eta=25)
    call_alert(stroke, "emt.garcia", "stroke")
    for case_id in (breathing, stroke):
        acknowledge(case_id)
    # Unacknowledged, the alert puts the stroke patient in the top group.
    body = queue()
    assert [c["id"] for c in body["cases"]] == [stroke, breathing]
    assert body["cases"][0]["queue_reasons"] == ["Crew alert not yet acknowledged", "Crew alert: Stroke", "ETA 25 min"]
    assert body["counts"]["crew_alerts"] == 1

    # Acknowledged, it still ranks above a patient the AI calls Prepare now who arrives sooner.
    acknowledge_alerts(stroke)
    body = queue()
    assert [c["id"] for c in body["cases"]] == [stroke, breathing]
    assert body["cases"][0]["queue_reasons"] == ["Crew alert: Stroke", "ETA 25 min"]

    # New information on any patient still comes first, alert or not.
    client.post(f"/api/cases/{breathing}/updates", json={"kind": "note", "body": "breathing worse"},
                headers=auth("emt.wilson"))
    assert [c["id"] for c in queue()["cases"]] == [breathing, stroke]
    acknowledge(breathing)

    # A cancelled alert: on top until acknowledged, then back to arrival order.
    response = client.post(f"/api/cases/{stroke}/alerts", json={"alert": "stroke", "action": "cancelled"},
                           headers=auth("emt.garcia"))
    assert response.status_code == 201, response.text
    assert [c["id"] for c in queue()["cases"]] == [stroke, breathing]
    acknowledge_alerts(stroke)
    assert [c["id"] for c in queue()["cases"]] == [breathing, stroke]


def test_alert_changes_are_recorded_in_the_queue_history(monkeypatch):
    score_as(monkeypatch, {"a": "Routine", "b": "Routine"})
    early = open_case("emt.wilson", "a patient", eta=5)
    late = open_case("emt.garcia", "b patient", eta=30)
    acknowledge(early)
    acknowledge(late)
    before = len(history())
    call_alert(late, "emt.garcia", "stemi")
    acknowledge_alerts(late)
    assert moves(history()[before:]) == [(late, "moved", 2, 1, "alert_called", late),
                                         (early, "moved", 1, 2, "alert_called", late)]
    assert history()[-1]["rule_version"] == "draft-4"


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
    # It ranks by arrival like everyone else (draft-4 doesn't rank on the AI's category).
    assert [c["id"] for c in body["cases"]] == [unsure, now]
    assert body["cases"][0]["queue_reasons"] == ["ETA 5 min"]
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


def history(username=DOCTOR, **params):
    response = client.get("/api/cases/queue/history", params=params, headers=auth(username))
    assert response.status_code == 200, response.text
    return response.json()


def moves(rows):
    return [(row["case_id"], row["change"], row["from_position"], row["to_position"], row["cause"], row["cause_case_id"])
            for row in rows]


def test_each_queue_change_is_recorded_once_with_its_cause(monkeypatch):
    score_as(monkeypatch, {"first": "Can wait", "second": "Can wait"})
    first = open_case("emt.wilson", "first patient", eta=5)
    second = open_case("emt.garcia", "second patient", eta=20)
    acknowledge(first)
    acknowledge(second)
    # Each step of the draft rule, with whose change caused it:
    assert moves(history()) == [
        (first, "entered", None, 1, "case_opened", first),
        # Both new: sooner arrival first. The assessments that follow move nobody.
        (second, "entered", None, 2, "case_opened", second),
        # Acknowledged information drops below unacknowledged information...
        (second, "moved", 2, 1, "acknowledged", first),
        (first, "moved", 1, 2, "acknowledged", first),
        # ...until both are acknowledged.
        (first, "moved", 2, 1, "acknowledged", second),
        (second, "moved", 1, 2, "acknowledged", second),
    ]
    seen = history()[-1]["revision"]

    # New information on the second patient puts it first; the first patient moves because of it.
    client.post(f"/api/cases/{second}/updates", json={"kind": "note", "body": "second patient worse"},
                headers=auth("emt.garcia"))
    changed = history(after_revision=seen)
    assert moves(changed) == [(second, "moved", 2, 1, "update_added", second),
                              (first, "moved", 1, 2, "update_added", second)]
    assert {row["rule_version"] for row in changed} == {"draft-4"}
    assert changed[0]["needs_attention"] == 1 and changed[1]["needs_attention"] == 0

    # Reading the queue or its history never records anything.
    total = len(history())
    queue()
    history()
    assert len(history()) == total

    # Leaving the queue is recorded too; the history stays.
    client.post(f"/api/cases/{first}/arrive", headers=auth(DOCTOR))
    assert moves(history()[total:]) == [(first, "left", 2, None, "arrived", first)]


def test_another_hospitals_queue_history_is_not_visible(monkeypatch):
    score_as(monkeypatch, {"x": "Can wait"})
    open_case("emt.lee", "x patient", eta=5, code="NORTH")
    assert history() == []
    assert len(history(OTHER_DOCTOR)) == 1
    assert client.get("/api/cases/queue/history", headers=auth("emt.lee")).status_code == 403
    assert client.get("/api/cases/queue/history?after_revision=x", headers=auth(DOCTOR)).status_code == 400


def test_startup_records_an_order_changed_without_a_case_event(monkeypatch):
    score_as(monkeypatch, {"x": "Can wait"})
    first = open_case("emt.wilson", "x patient", eta=5)
    second = open_case("emt.garcia", "x patient", eta=20)
    acknowledge(first)
    acknowledge(second)
    before = len(history())
    run("UPDATE cases SET eta_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (second,))  # e.g. a migration
    cases.record_all_queues("server_started")
    cases.record_all_queues("server_started")  # nothing new the second time
    assert moves(history()[before:]) == [(second, "moved", 2, 1, "server_started", None),
                                         (first, "moved", 1, 2, "server_started", None)]


def test_the_order_does_not_change_just_because_time_passes():
    from datetime import datetime, timezone
    def inbound(case_id, eta_at):
        return {"id": case_id, "status": "active", "arrived_at": None, "eta_at": eta_at,
                "started_at": f"2026-10-11T18:00:0{case_id}.000Z", "preparation_category": "Can wait",
                "latest_update_acknowledged": True, "processing": {"needs_review": False, "reasons": []}}
    # Both ETAs pass between the two looks; the one due first stays first.
    cases_ = [inbound(1, "2026-10-11T18:05:00.000Z"), inbound(2, "2026-10-11T18:03:00.000Z")]
    early = [c["id"] for c in case_queue.order([dict(c) for c in cases_], datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc))]
    late = [c["id"] for c in case_queue.order([dict(c) for c in cases_], datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc))]
    assert early == late == [2, 1]


# ---- AI-assisted: the AI's category as one more tier, below a person's calls ----------------------

def ai_assisted_case(case_id, category=None, eta="2026-10-15T15:20:00.000Z", acknowledged=True, alert=False,
               needs_review=False, alert_unacknowledged=False):
    """Only the stored fields the ordering reads, so the table below is a spec anyone can reimplement."""
    return {"id": case_id, "preparation_category": category, "eta_at": eta,
            "started_at": f"2026-10-15T15:00:0{case_id}.000Z", "latest_update_acknowledged": acknowledged,
            "processing": {"needs_review": needs_review},
            "crew_alerts": {"active": [{"alert": "stroke"}] if alert else [], "unacknowledged": alert_unacknowledged}}


@pytest.mark.parametrize("name, cases, expected", [
    ("the AI category orders cases that are otherwise equal",
     [ai_assisted_case(1, "Routine", "2026-10-15T15:05:00.000Z"), ai_assisted_case(2, "Can wait", "2026-10-15T15:10:00.000Z"),
      ai_assisted_case(3, "Prepare now", "2026-10-15T15:30:00.000Z")], [3, 2, 1]),
    ("arrival breaks ties inside one category",
     [ai_assisted_case(1, "Prepare now", "2026-10-15T15:30:00.000Z"), ai_assisted_case(2, "Prepare now", "2026-10-15T15:10:00.000Z")], [2, 1]),
    ("unacknowledged information outranks the AI",
     [ai_assisted_case(1, "Prepare now"), ai_assisted_case(2, "Routine", acknowledged=False)], [2, 1]),
    ("an unacknowledged crew alert outranks the AI",
     [ai_assisted_case(1, "Prepare now"), ai_assisted_case(2, "Routine", alert_unacknowledged=True)], [2, 1]),
    ("a processing failure outranks the AI",
     [ai_assisted_case(1, "Prepare now"), ai_assisted_case(2, None, needs_review=True)], [2, 1]),
    ("an active crew alert outranks the AI",
     [ai_assisted_case(1, "Prepare now"), ai_assisted_case(2, "Routine", alert=True)], [2, 1]),
    ("Cannot assess and no assessment share the unknown tier, between Prepare now and Can wait",
     [ai_assisted_case(1, "Can wait", "2026-10-15T15:05:00.000Z"), ai_assisted_case(2, "Cannot assess", "2026-10-15T15:20:00.000Z"),
      ai_assisted_case(3, None, "2026-10-15T15:10:00.000Z"), ai_assisted_case(4, "Prepare now", "2026-10-15T15:30:00.000Z")], [4, 3, 2, 1]),
    ("a case without an ETA goes last in its tier",
     [ai_assisted_case(1, "Can wait", None), ai_assisted_case(2, "Can wait", "2026-10-15T15:50:00.000Z")], [2, 1]),
])
def test_ai_assisted_ordering_table(name, cases, expected):
    assert [c["id"] for c in sorted(cases, key=case_queue.ai_assisted_key)] == expected, name


def test_ai_assisted_mode_reorders_by_the_ai_category_and_says_where_standard_would_put_each(monkeypatch):
    score_as(monkeypatch, {"routine": "Routine", "wait": "Can wait", "now": "Prepare now"})
    routine = open_case("emt.wilson", "routine patient", eta=5)
    wait = open_case("emt.garcia", "wait patient", eta=20)
    now = open_case("emt.lee", "now patient", eta=30)
    for case_id in (routine, wait, now):
        acknowledge(case_id)
    recorded = len(history())
    standard = queue()
    assert standard["queue_mode"] == "standard"
    assert [c["id"] for c in standard["cases"]] == [routine, wait, now]

    assert client.put("/api/hospitals/mine/queue-mode", json={"mode": "ai_assisted"}, headers=auth(DOCTOR)).status_code == 200
    assisted = queue()
    assert assisted["queue_mode"] == "ai_assisted" and assisted["auto_order"] is True
    assert [(c["id"], c["queue_position"], c["suggested_position"]) for c in assisted["cases"]] == [
        (now, 1, 3), (wait, 2, 2), (routine, 3, 1)]
    # "Why here" names what AI-assisted used, and the Standard rule's text and both versions are in the response.
    assert assisted["cases"][0]["queue_reasons"] == ["AI suggests Prepare now", "ETA 30 min"]
    assert assisted["rule_versions"] == {"standard": "draft-4", "ai_assisted": "ai-assisted-1"}
    assert assisted["rules"]["ai_assisted"] == case_queue.AI_ASSISTED_RULE and assisted["rules"]["standard"] == case_queue.QUEUE_RULE
    assert assisted["counts"] == standard["counts"]
    # Queue history keeps recording the Standard order: switching mode changes nothing on record.
    assert len(history()) == recorded

    client.put("/api/hospitals/mine/queue-mode", json={"mode": "standard"}, headers=auth(DOCTOR))
    assert [c["id"] for c in queue()["cases"]] == [routine, wait, now]


def test_ai_assisted_mode_keeps_a_persons_call_above_the_ai(monkeypatch):
    score_as(monkeypatch, {"calm": "Routine", "now": "Prepare now"})
    calm = open_case("emt.wilson", "calm patient", eta=30)
    now = open_case("emt.garcia", "now patient", eta=10)
    acknowledge(calm)
    acknowledge(now)
    call_alert(calm, "emt.wilson", "stroke")
    acknowledge_alerts(calm)
    client.put("/api/hospitals/mine/queue-mode", json={"mode": "ai_assisted"}, headers=auth(DOCTOR))
    assert [c["id"] for c in queue()["cases"]] == [calm, now]   # the crew's alert beats the AI's Prepare now
