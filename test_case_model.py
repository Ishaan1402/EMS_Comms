"""Case data model (KAN-7) and processing/failure states (KAN-11)."""
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import case_assessment
import database
from database import init_database, insert_sample_data, query, run
from middleware.auth import create_access_token
from routes import cases, recordings

client = TestClient(app, raise_server_exceptions=False)

EMT = "emt.wilson"
OTHER_EMT = "emt.garcia"
DOCTOR = "dr.smith"          # General Hospital
OTHER_DOCTOR = "dr.jones"    # Northside


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("case-model-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(recordings, "openai_client", None)
    monkeypatch.setattr(cases, "SEGMENT_DIR", tmp_path / "segments")
    monkeypatch.setattr(cases, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)


def auth(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def hospital_id(code="GEN"):
    return query("SELECT id FROM hospitals WHERE code = ?", (code,))[0]["id"]


def start_case(username=EMT, **fields):
    run(
        "UPDATE cases SET status = 'closed' WHERE status = 'active' AND emt_id = (SELECT id FROM users WHERE username = ?)",
        (username,),
    )
    body = {"patient_info": "67F short of breath", "destination_hospital_id": hospital_id(),
            "ems_unit": "Medic 12", "eta_minutes": 15, **fields}
    response = client.post("/api/cases", json=body, headers=auth(username))
    assert response.status_code == 201, response.text
    return response.json()


def get_case(case_id, username=DOCTOR):
    response = client.get(f"/api/cases/{case_id}", headers=auth(username))
    assert response.status_code == 200, response.text
    return response.json()


def add_update(case_id, username=EMT, **body):
    return client.post(f"/api/cases/{case_id}/updates", json=body, headers=auth(username))


USAGE = {"input_tokens": 1200, "output_tokens": 300, "latency_ms": 1500}


def use_scorer(monkeypatch, *results):
    """Each assessment takes the next output; an Exception instance is raised instead. Records inputs."""
    calls = []
    remaining = list(results)

    async def fake_score(inputs):
        calls.append(inputs)
        result = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(result, BaseException):
            raise result
        return result, USAGE

    monkeypatch.setattr(case_assessment, "score", fake_score)
    return calls


def output(category="Prepare now", change="No earlier report", **fields):
    """A valid answer in the evaluation contract's five fields."""
    return {"summary": "Adult short of breath, SpO2 falling.", "meaningful_change": change,
            "change_explanation": "No earlier report to compare.", "missing_information": "None identified",
            "preparation_category": category, **fields}


SCORED = output()


class TestCaseCreation:
    def test_hospitals_are_listed(self):
        hospitals = client.get("/api/hospitals", headers=auth(EMT)).json()
        assert {"GEN", "NORTH", "CHILD"} <= {h["code"] for h in hospitals}

    def test_case_records_routing_and_tracking_fields(self):
        case = start_case()
        assert case["destination_hospital_id"] == hospital_id()
        assert case["destination_hospital_name"] == "General Hospital (demo)"
        assert case["ems_unit"] == "Medic 12"
        assert case["eta_at"] > case["started_at"]
        assert case["info_version"] == 1
        assert case["operational_status"] == "inbound"
        assert case["latest_update_acknowledged"] is False

    def test_invalid_fields_are_rejected(self):
        run("UPDATE cases SET status = 'closed' WHERE status = 'active'")
        for body in ({"destination_hospital_id": 9999}, {"destination_hospital_id": "1"},
                     {"eta_minutes": -1}, {"eta_minutes": "soon"}, {"ems_unit": "x" * 41}):
            response = client.post("/api/cases", json=body, headers=auth(EMT))
            assert response.status_code == 400, body

    def test_hospital_users_only_see_cases_routed_to_their_hospital(self):
        north = start_case(destination_hospital_id=hospital_id("NORTH"))
        north_ids = [c["id"] for c in client.get("/api/cases", headers=auth(OTHER_DOCTOR)).json()]
        general_ids = [c["id"] for c in client.get("/api/cases", headers=auth(DOCTOR)).json()]
        assert north["id"] in north_ids and north["id"] not in general_ids
        for path in ("", "/segments", "/updates", "/vitals", "/assessments", "/messages"):
            assert client.get(f"/api/cases/{north['id']}{path}", headers=auth(DOCTOR)).status_code == 404, path
        assert client.post(f"/api/cases/{north['id']}/acknowledge", headers=auth(DOCTOR)).status_code == 404
        assert client.post(f"/api/cases/{north['id']}/messages", json={"body": "x"},
                           headers=auth(DOCTOR)).status_code == 404

    def test_live_stream_filter_matches_case_visibility(self):
        smith = query("SELECT id, username, role FROM users WHERE username = ?", (DOCTOR,))[0]
        emt = query("SELECT id, username, role FROM users WHERE username = ?", (EMT,))[0]
        accepts_smith, accepts_emt = cases.event_filter(smith), cases.event_filter(emt)
        assert accepts_smith(999, hospital_id("GEN")) and not accepts_smith(999, hospital_id("NORTH"))
        assert not accepts_smith(999, None)
        assert accepts_emt(emt["id"], hospital_id("NORTH")) and not accepts_emt(999, hospital_id("GEN"))

    def test_hospital_users_register_with_a_hospital(self):
        base = {"password": "pw-123456", "role": "doctor", "first_name": "Ana", "last_name": "Ruiz"}
        hospitals = client.get("/api/hospitals").json()  # public: the sign-up form needs it
        assert {"GEN", "NORTH", "CHILD"} <= {h["code"] for h in hospitals}
        bad = client.post("/api/auth/register", json={**base, "username": "dr.bad", "email": "b@x.org", "hospital_id": 999})
        assert bad.status_code == 400
        ok = client.post("/api/auth/register", json={**base, "username": "dr.ruiz", "email": "r@x.org",
                                                     "hospital_id": hospital_id("GEN")})
        assert ok.status_code == 201
        headers = {"Authorization": f"Bearer {ok.json()['token']}"}
        assert client.get("/api/hospitals/mine", headers=headers).json()["code"] == "GEN"
        general = start_case()
        assert general["id"] in [c["id"] for c in client.get("/api/cases", headers=headers).json()]

    def test_unassigned_hospital_users_see_no_cases(self):
        response = client.post("/api/auth/register", json={
            "username": "dr.none", "email": "n@x.org", "password": "pw-123456", "role": "doctor",
            "first_name": "No", "last_name": "Hospital"})
        headers = {"Authorization": f"Bearer {response.json()['token']}"}
        start_case()
        assert client.get("/api/cases", headers=headers).json() == []
        assert client.get("/api/hospitals/mine", headers=headers).json() is None
        assert client.get("/api/hospitals/mine", headers=auth(EMT)).json() is None


class TestUpdatesAndHistory:
    def test_vitals_history_is_kept_not_overwritten(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        case = start_case()
        for spo2 in (96, 91, 86):
            assert add_update(case["id"], kind="vitals", vitals={"spo2": spo2, "hr": 100 + spo2 % 10}).status_code == 201

        vitals = client.get(f"/api/cases/{case['id']}/vitals", headers=auth(DOCTOR)).json()
        assert [v["value"] for v in vitals if v["name"] == "spo2"] == [96, 91, 86]
        assert vitals[0]["unit"] == "%" and vitals[0]["label"] == "SpO2"
        assert get_case(case["id"])["info_version"] == 4

    def test_correction_is_appended_and_original_kept(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        case = start_case()
        add_update(case["id"], kind="note", body="Patient on home oxygen")
        add_update(case["id"], kind="correction", body="Not on home oxygen; that was a neighbor")
        updates = client.get(f"/api/cases/{case['id']}/updates", headers=auth(DOCTOR)).json()
        assert [u["kind"] for u in updates] == ["note", "correction"]
        assert [u["info_version"] for u in updates] == [2, 3]
        assert updates[0]["body"] == "Patient on home oxygen"
        assert updates[1]["author_role"] == "emt"

    def test_eta_change_is_logged_without_new_patient_version(self, monkeypatch):
        calls = use_scorer(monkeypatch, SCORED)
        case = start_case()
        calls.clear()
        response = add_update(case["id"], kind="eta", eta_minutes=3)
        assert response.status_code == 201
        after = get_case(case["id"])
        assert after["info_version"] == 1
        assert after["eta_at"] == response.json()["eta_at"] < case["eta_at"]
        assert calls == []

    def test_update_validation(self):
        case = start_case()
        bad = [
            {"kind": "gossip", "body": "x"},
            {"kind": "note"},
            {"kind": "note", "body": "   "},
            {"kind": "vitals", "vitals": {}},
            {"kind": "vitals", "vitals": {"spo2": 140}},
            {"kind": "vitals", "vitals": {"spo2": True}},
            {"kind": "vitals", "vitals": {"mood": 3}},
            {"kind": "eta"},
            {"kind": "note", "body": "x", "client_id": "y" * 65},
        ]
        for body in bad:
            assert add_update(case["id"], **body).status_code == 400, body

    def test_retried_update_is_not_duplicated(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        case = start_case()
        first = add_update(case["id"], kind="note", body="Pain 8/10", client_id="u-1")
        again = add_update(case["id"], kind="note", body="Pain 8/10", client_id="u-1")
        assert first.status_code == 201 and again.status_code == 200
        assert first.json()["id"] == again.json()["id"]
        assert get_case(case["id"])["info_version"] == 2

    def test_only_the_owning_emt_can_update_an_open_case(self):
        case = start_case()
        assert add_update(case["id"], username=OTHER_EMT, kind="note", body="x").status_code == 404
        assert add_update(case["id"], username=DOCTOR, kind="note", body="x").status_code == 403
        client.post(f"/api/cases/{case['id']}/close", headers=auth(EMT))
        assert add_update(case["id"], kind="note", body="x").status_code == 409


class TestOperationalStatus:
    def test_acknowledgment_tracks_the_latest_information(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        case = start_case()
        acked = client.post(f"/api/cases/{case['id']}/acknowledge", headers=auth(DOCTOR)).json()
        assert acked["operational_status"] == "acknowledged"
        assert acked["acknowledgment"]["info_version"] == 1
        assert acked["acknowledgment"]["user_name"] == "John Smith"

        add_update(case["id"], kind="vitals", vitals={"spo2": 88})
        after = get_case(case["id"])
        assert after["operational_status"] == "inbound"
        assert after["latest_update_acknowledged"] is False

    def test_acknowledging_an_older_version_leaves_newer_info_unacknowledged(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        case = start_case()
        add_update(case["id"], kind="note", body="New finding")
        acked = client.post(f"/api/cases/{case['id']}/acknowledge", json={"info_version": 1}, headers=auth(DOCTOR))
        assert acked.json()["latest_update_acknowledged"] is False
        too_new = client.post(f"/api/cases/{case['id']}/acknowledge", json={"info_version": 9}, headers=auth(DOCTOR))
        assert too_new.status_code == 400

    def test_emt_cannot_acknowledge(self):
        case = start_case()
        assert client.post(f"/api/cases/{case['id']}/acknowledge", headers=auth(EMT)).status_code == 403

    def test_arrived_then_closed(self):
        case = start_case()
        arrived = client.post(f"/api/cases/{case['id']}/arrive", headers=auth(DOCTOR)).json()
        assert arrived["operational_status"] == "arrived" and arrived["arrived_at"]
        again = client.post(f"/api/cases/{case['id']}/arrive", headers=auth(EMT)).json()
        assert again["arrived_at"] == arrived["arrived_at"]
        closed = client.post(f"/api/cases/{case['id']}/close", headers=auth(EMT)).json()
        assert closed["operational_status"] == "closed"


class TestAssessments:
    def test_completed_assessment_supplies_the_contract_fields_and_cost(self, monkeypatch):
        calls = use_scorer(monkeypatch, SCORED)
        case = start_case()
        after = get_case(case["id"])
        assessment = after["current_assessment"]
        assert after["preparation_category"] == "Prepare now"
        assert assessment["meaningful_change"] == "No earlier report"
        assert assessment["summary"] == "Adult short of breath, SpO2 falling."
        assert assessment["based_on_version"] == 1 and assessment["baseline_version"] is None
        assert assessment["scorer_version"] == f"{recordings.SCORING_MODEL}/awareness_v2"
        assert (assessment["input_tokens"], assessment["output_tokens"], assessment["latency_ms"]) == (1200, 300, 1500)
        assert assessment["cost_usd"] == pytest.approx((1200 * 0.10 + 300 * 0.50) / 1_000_000)
        assert after["processing"]["status"] == "completed"
        assert after["processing"]["needs_review"] is False
        model_input = calls[0]["input"]
        assert set(model_input) == {"case_id", "encounter_id", "update_number", "elapsed_minutes",
                                    "eta_minutes", "prior_information", "current_transcript"}
        assert model_input["prior_information"] == case_assessment.NO_EARLIER_REPORT
        assert model_input["current_transcript"] == "Typed by the crew: 67F short of breath"
        assert model_input["update_number"] == 1 and model_input["eta_minutes"] in (14, 15)

    def test_unknown_model_price_gives_no_cost(self):
        assert case_assessment.estimate_cost("some-new-model", USAGE) is None
        assert case_assessment.estimate_cost("gpt-6-luna", {"input_tokens": None, "output_tokens": 3}) is None

    def test_missing_scorer_fails_visibly_without_a_score(self):
        case = start_case()
        after = get_case(case["id"])
        assert after["preparation_category"] is None
        assert after["processing"]["status"] == "failed"
        assert after["processing"]["needs_review"] is True
        assert after["processing"]["reasons"] == ["Assessment failed: AI scoring is not configured"]
        assert after["processing"]["assessment"]["can_retry"] is True

    @pytest.mark.parametrize("result", [
        None,                                              # unparseable JSON
        "not an object",
        {k: v for k, v in SCORED.items() if k != "summary"},
        output(category="Urgent"),                         # not one of the categories
        output(change="Maybe"),
        output(summary="   "),
        output(extra="field"),
    ])
    def test_unreadable_answers_need_review_without_a_category(self, monkeypatch, result):
        use_scorer(monkeypatch, result)
        after = get_case(start_case()["id"])
        assert after["preparation_category"] is None
        assert after["current_assessment"]["status"] == "needs_review"
        assert after["processing"]["reasons"] == [case_assessment.UNREADABLE_REASON]

    def test_unsure_is_stored_as_cannot_assess_and_needs_review(self, monkeypatch):
        use_scorer(monkeypatch, output(category="Unsure"))
        after = get_case(start_case()["id"])
        assert after["preparation_category"] == "Cannot assess"
        assert after["current_assessment"]["status"] == "needs_review"
        assert after["processing"]["reasons"] == ["Insufficient information to assess preparation needs."]

    def test_failure_keeps_the_previous_assessment_and_flags_it_outdated(self, monkeypatch):
        use_scorer(monkeypatch, SCORED, RuntimeError("model outage"))
        case = start_case()
        add_update(case["id"], kind="vitals", vitals={"spo2": 85})
        after = get_case(case["id"])
        assert after["preparation_category"] == "Prepare now"
        assert after["current_assessment"]["based_on_version"] == 1
        assert after["assessment_is_outdated"] is True
        assert after["processing"]["status"] == "failed"
        assert after["processing"]["reasons"] == ["Assessment failed: unexpected error"]

        vitals = client.get(f"/api/cases/{case['id']}/vitals", headers=auth(DOCTOR)).json()
        assert [v["value"] for v in vitals] == [85]
        history = client.get(f"/api/cases/{case['id']}/assessments", headers=auth(DOCTOR)).json()
        assert [a["status"] for a in history] == ["failed", "completed"]

    def test_retry_only_after_a_failure(self, monkeypatch):
        use_scorer(monkeypatch, RuntimeError("timeout"), SCORED)
        case = start_case()
        retried = client.post(f"/api/cases/{case['id']}/assessments/retry", headers=auth(DOCTOR))
        assert retried.status_code == 202
        after = get_case(case["id"])
        assert after["preparation_category"] == "Prepare now" and after["processing"]["status"] == "completed"
        again = client.post(f"/api/cases/{case['id']}/assessments/retry", headers=auth(DOCTOR))
        assert again.status_code == 409

    def test_late_result_for_older_information_does_not_replace_newer(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        case = start_case()
        run("UPDATE cases SET info_version = 3 WHERE id = ?", (case["id"],))
        now = database.utc_now_iso()
        insert = """INSERT INTO risk_assessments (case_id, based_on_version, status, preparation_category,
                        scorer_version, started_at, completed_at, updated_at) VALUES (?, ?, 'completed', ?, 't', ?, ?, ?)"""
        run(insert, (case["id"], 3, "Prepare now", now, now, now))
        run(insert, (case["id"], 2, "Routine", now, now, now))  # older information, finished later
        after = get_case(case["id"])
        assert after["preparation_category"] == "Prepare now"
        assert after["current_assessment"]["based_on_version"] == 3

    def test_transcript_text_is_new_information_and_is_assessed(self, monkeypatch):
        calls = use_scorer(monkeypatch, SCORED)
        monkeypatch.setattr(cases, "transcribe_segment_sync", lambda path, prompt: "SpO2 dropping to 86 on room air")
        case = start_case()
        response = client.post(
            f"/api/cases/{case['id']}/segments",
            files={"audio": ("segment-0.webm", b"audio", "audio/webm")},
            data={"seq": "0", "client_id": "clip-0", "recorded_at": "2026-01-01T15:00:00Z"},
            headers=auth(EMT),
        )
        assert response.status_code == 201
        after = get_case(case["id"])
        assert after["info_version"] == 2
        assert after["current_assessment"]["based_on_version"] == 2
        assert calls[-1]["input"]["current_transcript"].endswith("SpO2 dropping to 86 on room air")
        segment = client.get(f"/api/cases/{case['id']}/segments", headers=auth(DOCTOR)).json()[0]
        assert segment["info_version"] == 2

    def test_failed_transcription_marks_case_for_review(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)

        def broken(path, prompt):
            raise case_assessment.openai.BadRequestError("bad audio", response=SimpleNamespace(
                request=None, status_code=400, headers={}), body=None)
        monkeypatch.setattr(cases, "transcribe_segment_sync", broken)
        case = start_case()
        client.post(
            f"/api/cases/{case['id']}/segments",
            files={"audio": ("segment-0.webm", b"audio", "audio/webm")},
            data={"seq": "0", "client_id": "clip-0"},
            headers=auth(EMT),
        )
        after = get_case(case["id"])
        assert after["info_version"] == 1
        assert after["preparation_category"] == "Prepare now"  # the earlier assessment is untouched
        assert after["processing"]["transcription"] == {"status": "failed", "pending": 0, "failed": 1}
        assert after["processing"]["needs_review"] is True
        assert after["processing"]["reasons"] == ["1 transcript segment(s) failed to transcribe"]

    def test_interrupted_assessments_are_failed_at_startup(self):
        case = start_case()
        now = database.utc_now_iso()
        run("""INSERT INTO risk_assessments (case_id, based_on_version, status, scorer_version, started_at, updated_at)
               VALUES (?, 1, 'processing', 't', ?, ?)""", (case["id"], now, now))
        case_assessment.fail_interrupted_assessments()
        after = get_case(case["id"])
        assert after["processing"]["assessment"]["status"] == "failed"
        assert "server restart" in after["processing"]["reasons"][0]


class TestModelInput:
    def test_earlier_information_is_what_the_hospital_acknowledged(self, monkeypatch):
        calls = use_scorer(monkeypatch, SCORED)
        case = start_case()
        client.post(f"/api/cases/{case['id']}/acknowledge", headers=auth(DOCTOR))
        add_update(case["id"], kind="vitals", vitals={"spo2": 88}, body="on room air")
        model_input = calls[-1]["input"]
        assert model_input["prior_information"] == "Typed by the crew: 67F short of breath"
        assert model_input["current_transcript"] == "Vitals typed by the crew: SpO2 88 % — on room air"
        assert model_input["update_number"] == 2
        assert get_case(case["id"])["current_assessment"]["baseline_version"] == 1

    def test_before_any_acknowledgment_everything_is_current(self, monkeypatch):
        calls = use_scorer(monkeypatch, SCORED)
        case = start_case()
        add_update(case["id"], kind="note", body="Now speaking in short phrases")
        model_input = calls[-1]["input"]
        assert model_input["prior_information"] == case_assessment.NO_EARLIER_REPORT
        assert model_input["current_transcript"] == (
            "Typed by the crew: 67F short of breath\nTyped by the crew: Now speaking in short phrases")

    def test_radio_segments_run_together_as_speech(self, monkeypatch):
        calls = use_scorer(monkeypatch, SCORED)
        texts = {0: "Heart rate 126, BP 100", 1: "over 64, saturation 89 percent."}
        monkeypatch.setattr(cases, "transcribe_segment_sync",
                            lambda path, prompt: texts[int(path.split("-seg-")[1].split("-")[0])])
        case = start_case(patient_info=None)
        for seq in (0, 1):
            client.post(f"/api/cases/{case['id']}/segments", files={"audio": (f"s{seq}.webm", b"a", "audio/webm")},
                        data={"seq": str(seq), "client_id": f"c{seq}"}, headers=auth(EMT))
        assert calls[-1]["input"]["current_transcript"] == "Heart rate 126, BP 100 over 64, saturation 89 percent."

    def test_contract_matches_the_evaluation_harness(self):
        evals_contracts = Path(__file__).parent / "evals" / "contracts.py"
        if not evals_contracts.exists():
            pytest.skip("evals/contracts.py is on vrishank-branch until it is merged")
        from evals import contracts
        assert case_assessment.CHANGE == contracts.CHANGE
        assert case_assessment.PREPARATION == contracts.PREPARATION
        assert case_assessment.OUTPUT_FIELDS == contracts.OUTPUT_FIELDS

    def test_prompt_is_the_evaluation_prompt(self):
        assert case_assessment.PROMPT.startswith("You organize fictional EMS reports")
        assert case_assessment.PROMPT_VERSION == "awareness_v2"


class TestScorerRequest:
    def test_sends_the_evaluation_prompt_and_strict_schema(self, monkeypatch):
        sent = {}

        def create(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(SCORED), refusal=None))],
                usage=SimpleNamespace(prompt_tokens=900, completion_tokens=120))
        monkeypatch.setattr(recordings, "openai_client",
                            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        inputs = {"input": {"case_id": "case-1", "current_transcript": "x"}}
        result, usage = asyncio.run(case_assessment.score(inputs))
        assert result == SCORED
        assert (usage["input_tokens"], usage["output_tokens"]) == (900, 120)
        assert sent["messages"][0] == {"role": "system", "content": case_assessment.PROMPT}
        assert json.loads(sent["messages"][1]["content"]) == inputs["input"]
        schema = sent["response_format"]["json_schema"]
        assert schema["strict"] is True and schema["schema"] == case_assessment.OUTPUT_SCHEMA
        assert sent["timeout"] == recordings.SCORING_REQUEST_TIMEOUT_SECONDS

    def test_refusal_fails_the_assessment(self, monkeypatch):
        def create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None, refusal="no"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        monkeypatch.setattr(recordings, "openai_client",
                            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        after = get_case(start_case()["id"])
        assert after["processing"]["reasons"] == ["Assessment failed: the AI declined to assess this case"]


class TestDismissFailedSegments:
    def failed_case(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)

        def broken(path, prompt):
            raise case_assessment.openai.BadRequestError("bad audio", response=SimpleNamespace(
                request=None, status_code=400, headers={}), body=None)
        monkeypatch.setattr(cases, "transcribe_segment_sync", broken)
        case = start_case()
        segment = client.post(f"/api/cases/{case['id']}/segments", files={"audio": ("s.webm", b"a", "audio/webm")},
                              data={"seq": "0", "client_id": "c0"}, headers=auth(EMT)).json()
        return case, segment

    def test_dismissed_failure_stops_flagging_the_case_but_stays_on_record(self, monkeypatch):
        case, segment = self.failed_case(monkeypatch)
        assert get_case(case["id"])["processing"]["needs_review"] is True
        dismissed = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/dismiss", headers=auth(EMT))
        assert dismissed.status_code == 200
        assert dismissed.json()["dismissed_by_name"] == "Mike Wilson" and dismissed.json()["status"] == "failed"
        after = get_case(case["id"])
        assert after["processing"]["needs_review"] is False
        assert after["processing"]["transcription"]["failed"] == 0
        again = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/dismiss", headers=auth(DOCTOR))
        assert again.status_code == 200 and again.json()["dismissed_by_name"] == "Mike Wilson"

    def test_only_failed_segments_can_be_dismissed(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        monkeypatch.setattr(cases, "transcribe_segment_sync", lambda path, prompt: "fine")
        case = start_case()
        segment = client.post(f"/api/cases/{case['id']}/segments", files={"audio": ("s.webm", b"a", "audio/webm")},
                              data={"seq": "0", "client_id": "c0"}, headers=auth(EMT)).json()
        response = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/dismiss", headers=auth(EMT))
        assert response.status_code == 409

    def test_retry_clears_the_dismissal(self, monkeypatch):
        case, segment = self.failed_case(monkeypatch)
        client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/dismiss", headers=auth(DOCTOR))
        retried = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/retry", headers=auth(EMT)).json()
        assert retried["dismissed_at"] is None

    def test_other_hospitals_cannot_dismiss(self, monkeypatch):
        case, segment = self.failed_case(monkeypatch)
        response = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/dismiss", headers=auth(OTHER_DOCTOR))
        assert response.status_code == 404


class TestStartupRecovery:
    def test_open_cases_without_a_usable_assessment_are_found(self, monkeypatch):
        use_scorer(monkeypatch, SCORED)
        assessed = start_case()
        use_scorer(monkeypatch, RuntimeError("outage"))
        failed = start_case(username=OTHER_EMT)
        run("UPDATE cases SET info_version = info_version + 1 WHERE id = ?", (assessed["id"],))  # new info, not yet scored
        found = case_assessment.unassessed_open_cases()
        assert assessed["id"] in found and failed["id"] in found
        client.post(f"/api/cases/{failed['id']}/close", headers=auth(OTHER_EMT))
        assert failed["id"] not in case_assessment.unassessed_open_cases()

    def test_started_assessment_runs_in_the_background(self, monkeypatch):
        case = start_case()
        use_scorer(monkeypatch, SCORED)

        async def scenario():
            case_assessment.runner.start(case["id"], force=True)
            while case_assessment.runner._tasks:
                await asyncio.sleep(0.01)

        asyncio.run(scenario())
        assert get_case(case["id"])["preparation_category"] == "Prepare now"


class TestScoringRequest:
    def capture(self, monkeypatch):
        sent = {}

        def create(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"risk_score": 3}'))])
        monkeypatch.setattr(recordings, "openai_client",
                            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        return sent

    def test_request_has_a_timeout_and_reasoning_effort(self, monkeypatch):
        sent = self.capture(monkeypatch)
        asyncio.run(recordings.analyze_with_llm("transcript", "info"))
        assert sent["model"] == recordings.SCORING_MODEL
        assert sent["timeout"] == recordings.SCORING_REQUEST_TIMEOUT_SECONDS
        assert sent["extra_body"]["reasoning_effort"] == "none"

    def test_blank_reasoning_effort_is_not_sent(self, monkeypatch):
        sent = self.capture(monkeypatch)
        monkeypatch.setattr(recordings, "SCORING_REASONING_EFFORT", "")
        asyncio.run(recordings.analyze_with_llm("transcript", "info"))
        assert "reasoning_effort" not in sent["extra_body"]


class TestAssessmentRunner:
    def test_information_arriving_mid_assessment_is_assessed_once_more(self, monkeypatch):
        case = start_case()
        started = []
        release = None

        async def slow_score(inputs):
            started.append(inputs["version"])
            if len(started) == 1:
                await release.wait()
            return SCORED, USAGE

        monkeypatch.setattr(case_assessment, "score", slow_score)

        async def scenario():
            nonlocal release
            release = asyncio.Event()
            runner = case_assessment.AssessmentRunner()
            run("UPDATE cases SET info_version = info_version + 1 WHERE id = ?", (case["id"],))
            first = asyncio.create_task(runner.request(case["id"]))
            for _ in range(100):
                if started:
                    break
                await asyncio.sleep(0.01)
            assert started, "the first assessment never started"
            for _ in range(3):  # three more updates while the first assessment runs
                run("UPDATE cases SET info_version = info_version + 1 WHERE id = ?", (case["id"],))
                await runner.request(case["id"])
            release.set()
            await first

        asyncio.run(scenario())
        assert started == [started[0], started[0] + 3]


class TestCaseMessages:
    def test_emt_and_hospital_message_on_the_case(self):
        case = start_case()
        sent = client.post(f"/api/cases/{case['id']}/messages", json={"body": "ETA 5, on CPAP"}, headers=auth(EMT))
        assert sent.status_code == 201
        assert sent.json()["case_id"] == case["id"] and sent.json()["recording_id"] is None
        reply = client.post(f"/api/cases/{case['id']}/messages", json={"body": "Bay 3 ready"}, headers=auth(DOCTOR))
        assert reply.status_code == 201
        history = client.get(f"/api/cases/{case['id']}/messages", headers=auth(EMT)).json()
        assert history["case_id"] == case["id"]
        assert [m["body"] for m in history["messages"]] == ["ETA 5, on CPAP", "Bay 3 ready"]

    def test_other_emts_cannot_see_the_thread(self):
        case = start_case()
        assert client.get(f"/api/cases/{case['id']}/messages", headers=auth(OTHER_EMT)).status_code == 404
        assert client.post(f"/api/cases/{case['id']}/messages", json={"body": "x"},
                           headers=auth(OTHER_EMT)).status_code == 404

    def test_client_id_cannot_cross_from_a_recording_thread_to_a_case(self):
        case = start_case()
        emt_id = query("SELECT id FROM users WHERE username = ?", (EMT,))[0]["id"]
        recording_id = run("INSERT INTO recordings (emt_id, audio_file_path) VALUES (?, 'x')", (emt_id,))["id"]
        first = client.post(f"/api/recordings/{recording_id}/messages", json={"body": "a", "client_id": "m-1"},
                            headers=auth(EMT))
        assert first.status_code == 201
        reused = client.post(f"/api/cases/{case['id']}/messages", json={"body": "a", "client_id": "m-1"},
                             headers=auth(EMT))
        assert reused.status_code == 409

    def test_a_message_belongs_to_exactly_one_thread(self):
        with pytest.raises(sqlite3.IntegrityError):
            run("INSERT INTO messages (sender_id, sender_role, body) VALUES (1, 'emt', 'orphan')")


class TestLegacyRecordingScores:
    def test_uploaded_recording_has_no_score_until_scored(self):
        response = client.post(
            "/api/recordings/upload",
            files={"audio": ("r.webm", b"audio", "audio/webm")},
            headers=auth(EMT),
        )
        row = query("SELECT risk_score, priority_level FROM recordings WHERE id = ?",
                    (response.json()["recording"]["id"],))[0]
        assert row == {"risk_score": None, "priority_level": None}

    def test_missing_model_score_is_not_replaced_by_a_default(self, monkeypatch):
        monkeypatch.setattr(recordings, "transcribe_audio_sync", lambda path: "patient fell")

        async def no_score(transcription, patient_info=""):
            return {"chief_complaint": "Fall", "medical_summary": "Fall, unclear injuries"}
        monkeypatch.setattr(recordings, "analyze_with_llm", no_score)
        emt_id = query("SELECT id FROM users WHERE username = ?", (EMT,))[0]["id"]
        recording_id = run("INSERT INTO recordings (emt_id, audio_file_path) VALUES (?, 'x')", (emt_id,))["id"]
        asyncio.run(recordings.process_recording(recording_id, "x"))
        row = query("SELECT risk_score, priority_level, chief_complaint FROM recordings WHERE id = ?", (recording_id,))[0]
        assert row == {"risk_score": None, "priority_level": None, "chief_complaint": "Fall"}


class TestMigration:
    def test_database_from_before_cases_model_is_upgraded(self, tmp_path):
        old_db = tmp_path / "old.db"
        conn = sqlite3.connect(old_db)
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, email TEXT, password_hash TEXT,
                role TEXT, first_name TEXT, last_name TEXT, phone TEXT, specialty TEXT, is_available BOOLEAN DEFAULT 1,
                created_at DATETIME, updated_at DATETIME);
            CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, recording_id INTEGER, doctor_id INTEGER,
                notification_type TEXT, sent_at DATETIME, delivered BOOLEAN, read_at DATETIME, response TEXT);
            CREATE TABLE cases (id INTEGER PRIMARY KEY AUTOINCREMENT, emt_id INTEGER NOT NULL, patient_info TEXT,
                status TEXT NOT NULL DEFAULT 'active', started_at TEXT NOT NULL, closed_at TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, recording_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL, sender_role TEXT NOT NULL, body TEXT NOT NULL, client_id TEXT,
                created_at TEXT NOT NULL DEFAULT 'x');
            CREATE TABLE recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, emt_id INTEGER, audio_file_path TEXT NOT NULL,
                status TEXT DEFAULT 'pending');
            INSERT INTO recordings (id, emt_id, audio_file_path) VALUES (7, 1, 'x');
            INSERT INTO cases (emt_id, started_at, updated_at) VALUES (1, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z');
            INSERT INTO messages (recording_id, sender_id, sender_role, body) VALUES (7, 1, 'emt', 'kept');
        """)
        conn.commit()
        conn.close()

        previous = database.DB_PATH
        database.DB_PATH = old_db
        try:
            init_database()
            init_database()  # idempotent
            migrated = query("SELECT info_version, last_update_at, destination_hospital_id FROM cases")[0]
            assert migrated == {"info_version": 1, "last_update_at": "2026-01-01T00:00:00.000Z",
                                "destination_hospital_id": None}
            message = query("SELECT recording_id, case_id, body FROM messages")[0]
            assert message == {"recording_id": 7, "case_id": None, "body": "kept"}
        finally:
            database.DB_PATH = previous
