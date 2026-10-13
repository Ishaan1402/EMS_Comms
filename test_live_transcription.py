import asyncio
import copy
import json
import os
import socket
import threading
import time

import httpx
import openai
import pytest
import uvicorn
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import database
from database import init_database, insert_sample_data, query
from middleware.auth import create_access_token
from realtime import EventBroker
import case_assessment
import openai_config
from routes import cases

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("live-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(openai_config, "openai_client", None)
    monkeypatch.setattr(cases, "SEGMENT_DIR", tmp_path / "segments")
    monkeypatch.setattr(cases, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)


def auth_headers(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


EMT = "emt.wilson"
OTHER_EMT = "emt.garcia"
DOCTOR = "dr.smith"


def start_case(username=EMT, patient_info="54M chest pain"):
    # An EMT may only have one open case; close leftovers from earlier tests.
    database.run(
        "UPDATE cases SET status = 'closed' WHERE status = 'active' AND emt_id = (SELECT id FROM users WHERE username = ?)",
        (username,),
    )
    # Routed to the doctor's hospital: hospital users only see cases sent to them.
    hospital_id = query("SELECT hospital_id FROM users WHERE username = ?", (DOCTOR,))[0]["hospital_id"]
    response = client.post("/api/cases", json={"patient_info": patient_info, "destination_hospital_id": hospital_id},
                           headers=auth_headers(username))
    assert response.status_code == 201, response.text
    return response.json()


def upload(case_id, seq, username=EMT, recorded_at="2026-01-01T15:00:00.000Z", audio=b"fake-webm-bytes",
           client_id=None, **fields):
    data = {"seq": str(seq), "client_id": client_id or f"clip-{case_id}-{seq}",
            "recorded_at": recorded_at, "duration_ms": "8000", **fields}
    return client.post(
        f"/api/cases/{case_id}/segments",
        files={"audio": (f"segment-{seq}.webm", audio, "audio/webm;codecs=opus")},
        data=data,
        headers=auth_headers(username),
    )


def fake_transcriber(texts):
    """Returns texts keyed by the seq embedded in the uploaded file name."""
    def transcribe(audio_path, prompt):
        seq = int(audio_path.split("-seg-")[1].split("-")[0])
        return texts[seq]
    return transcribe


class TestCaseLifecycle:
    def test_emt_cannot_have_two_active_cases(self):
        start_case()
        second = client.post("/api/cases", json={}, headers=auth_headers(EMT))
        assert second.status_code == 409
        active = client.get("/api/cases?status=active", headers=auth_headers(EMT)).json()
        assert len(active) == 1

    def test_emt_starts_case_and_doctor_sees_it_as_active(self):
        case = start_case()
        assert case["status"] == "active"
        assert case["patient_info"] == "54M chest pain"
        assert case["started_at"].endswith("Z")

        listed = client.get("/api/cases?status=active", headers=auth_headers(DOCTOR)).json()
        assert case["id"] in [c["id"] for c in listed]

    def test_doctor_cannot_start_case(self):
        response = client.post("/api/cases", json={}, headers=auth_headers(DOCTOR))
        assert response.status_code == 403

    def test_close_case(self):
        case = start_case()
        closed = client.post(f"/api/cases/{case['id']}/close", headers=auth_headers(EMT)).json()
        assert closed["status"] == "closed"
        assert closed["closed_at"]

    def test_emt_only_sees_own_cases(self):
        mine = start_case(EMT)
        theirs = start_case(OTHER_EMT)
        listed_ids = [c["id"] for c in client.get("/api/cases", headers=auth_headers(EMT)).json()]
        assert mine["id"] in listed_ids
        assert theirs["id"] not in listed_ids
        assert client.get(f"/api/cases/{theirs['id']}", headers=auth_headers(EMT)).status_code == 404
        assert client.get(f"/api/cases/{theirs['id']}/segments", headers=auth_headers(EMT)).status_code == 404


class TestSegments:
    def test_segments_transcribed_and_returned_in_chronological_order(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "first", 1: " second ", 2: "third"}))
        case = start_case()

        # Uploads arrive out of order; seq decides the order.
        for seq in (2, 0, 1):
            response = upload(case["id"], seq, recorded_at=f"2026-01-01T15:00:{seq * 8:02d}.000Z")
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "pending"
            assert response.json()["case_id"] == case["id"]

        segments = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(DOCTOR)).json()
        assert [s["seq"] for s in segments] == [0, 1, 2]
        assert [s["text"] for s in segments] == ["first", "second", "third"]
        assert all(s["status"] == "completed" and s["transcribed_at"] for s in segments)
        assert segments[1]["recorded_at"] == "2026-01-01T15:00:08.000Z"
        assert "audio_file_path" not in segments[0]

        case_after = client.get(f"/api/cases/{case['id']}", headers=auth_headers(DOCTOR)).json()
        assert case_after["segment_count"] == 3

    def test_previous_segment_text_is_passed_as_prompt(self, monkeypatch):
        prompts = []

        def transcribe(audio_path, prompt):
            prompts.append(prompt)
            return "BP 90 over 60"

        monkeypatch.setattr(cases, "transcribe_segment_sync", transcribe)
        case = start_case()
        upload(case["id"], 0)
        upload(case["id"], 1)
        assert prompts == [None, "BP 90 over 60"]

    def test_recorded_at_timezone_normalized_to_utc(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "x"}))
        case = start_case()
        segment = upload(case["id"], 0, recorded_at="2026-01-01T08:00:00-07:00").json()
        assert segment["recorded_at"] == "2026-01-01T15:00:00.000Z"

    def test_recorded_at_corrected_for_client_clock_offset(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "x"}))
        case = start_case()
        fast_clock = datetime.now(timezone.utc) + timedelta(hours=1)  # client clock runs an hour fast
        recorded = fast_clock - timedelta(seconds=10)
        segment = upload(case["id"], 0, recorded_at=recorded.isoformat(), sent_at=fast_clock.isoformat()).json()
        lag = datetime.now(timezone.utc) - datetime.fromisoformat(segment["recorded_at"].replace("Z", "+00:00"))
        assert timedelta(seconds=9) < lag < timedelta(seconds=12)

    def test_same_seq_from_a_different_clip_is_rejected(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "original"}))
        case = start_case()
        upload(case["id"], 0, client_id="clip-a")
        clash = upload(case["id"], 0, client_id="clip-b")
        assert clash.status_code == 409
        assert "already exists" in clash.json()["error"]

    def test_malformed_numbers_are_400_not_500(self):
        case = start_case()
        assert upload(case["id"], "99999999999999999999999").status_code == 400
        assert upload(case["id"], "1.5").status_code == 400
        assert upload(case["id"], 0, client_id="x" * 65).status_code == 400
        for bad in ("inf", "nan", "1e30", "-5", "abc"):
            response = client.post(
                f"/api/cases/{case['id']}/segments",
                files={"audio": ("s.webm", b"x", "audio/webm")},
                data={"seq": "0", "client_id": "c", "duration_ms": bad},
                headers=auth_headers(EMT),
            )
            assert response.status_code == 400, bad

    def test_extreme_timestamps_fall_back_to_server_time(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "x", 1: "y"}))
        case = start_case()
        assert upload(case["id"], 0, recorded_at="0001-01-01T00:00:00+01:00").status_code == 201
        assert upload(case["id"], 1, recorded_at="2026-01-01T00:00:00Z", sent_at="9999-12-31T23:59:59-01:00").status_code == 201

    def test_duplicate_seq_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "once"}))
        case = start_case()
        first = upload(case["id"], 0)
        again = upload(case["id"], 0)
        assert first.status_code == 201
        assert again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        count = query("SELECT COUNT(*) AS n FROM transcript_segments WHERE case_id = ?", (case["id"],))[0]["n"]
        assert count == 1

    def test_missing_openai_key_marks_segment_failed_with_reason(self):
        case = start_case()
        upload(case["id"], 0)
        segment = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(DOCTOR)).json()[0]
        assert segment["status"] == "failed"
        assert segment["error"] == "transcription service is not configured"
        assert segment["text"] is None

    def test_transient_error_is_retried_automatically(self, monkeypatch):
        calls = []

        def flaky(audio_path, prompt):
            calls.append(1)
            if len(calls) == 1:
                raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
            return "recovered"

        monkeypatch.setattr(cases, "transcribe_segment_sync", flaky)
        case = start_case()
        upload(case["id"], 0)
        segment = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(EMT)).json()[0]
        assert segment["status"] == "completed"
        assert segment["text"] == "recovered"
        assert segment["attempts"] == 2

    def test_failed_segment_can_be_retried_by_emt(self, monkeypatch):
        def broken(audio_path, prompt):
            raise RuntimeError("boom")

        monkeypatch.setattr(cases, "transcribe_segment_sync", broken)
        case = start_case()
        segment = upload(case["id"], 0).json()
        failed = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(EMT)).json()[0]
        assert failed["status"] == "failed"
        assert failed["error"] == "unexpected error"

        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "second try"}))
        response = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/retry", headers=auth_headers(EMT))
        assert response.status_code == 200
        done = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(EMT)).json()[0]
        assert done["status"] == "completed"
        assert done["text"] == "second try"
        assert done["error"] is None

        again = client.post(f"/api/cases/{case['id']}/segments/{segment['id']}/retry", headers=auth_headers(EMT))
        assert again.status_code == 409

    def test_upload_rejections(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "x"}))
        case = start_case()
        other = start_case(OTHER_EMT)

        assert upload(other["id"], 0).status_code == 404  # another EMT's case
        assert upload(case["id"], 0, username=DOCTOR).status_code == 403
        assert upload(case["id"], -1).status_code == 400
        assert upload(case["id"], 0, audio=b"").status_code == 400

        bad_type = client.post(
            f"/api/cases/{case['id']}/segments",
            files={"audio": ("x.txt", b"hello", "text/plain")},
            data={"seq": "0", "client_id": "c"},
            headers=auth_headers(EMT),
        )
        assert bad_type.status_code == 400

        client.post(f"/api/cases/{case['id']}/close", headers=auth_headers(EMT))
        closed = upload(case["id"], 0)
        assert closed.status_code == 409
        assert closed.json() == {"error": "Case is closed"}


    def test_upload_racing_a_close_is_rejected_and_audio_removed(self, monkeypatch):
        case = start_case()
        stale_snapshot = dict(case)  # what the route read just before the close landed
        client.post(f"/api/cases/{case['id']}/close", headers=auth_headers(EMT))
        monkeypatch.setattr(cases, "get_case_for_user", lambda case_id, user: stale_snapshot)

        response = upload(case["id"], 0)
        assert response.status_code == 409
        assert not any(cases.SEGMENT_DIR.glob("*"))
        count = query("SELECT COUNT(*) AS n FROM transcript_segments WHERE case_id = ?", (case["id"],))[0]["n"]
        assert count == 0

    def test_unexpected_error_outside_whisper_still_marks_failed(self, monkeypatch):
        def broken_prompt(segment):
            raise RuntimeError("db hiccup")

        monkeypatch.setattr(cases, "previous_segment_text", broken_prompt)
        case = start_case()
        upload(case["id"], 0)
        segment = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(EMT)).json()[0]
        assert segment["status"] == "failed"
        assert segment["error"] == "unexpected error"

    def test_pending_segments_failed_on_startup(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment", lambda segment_id: None)  # simulate a crash mid-transcription
        case = start_case()
        upload(case["id"], 0)
        assert client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(EMT)).json()[0]["status"] == "pending"

        cases.fail_interrupted_segments()
        segment = client.get(f"/api/cases/{case['id']}/segments", headers=auth_headers(EMT)).json()[0]
        assert segment["status"] == "failed"
        assert segment["error"] == "interrupted by a server restart"


def end_report(case_id, report_id, clips, username=EMT):
    return client.post(f"/api/cases/{case_id}/reports/{report_id}/end", json={"clips": clips},
                       headers=auth_headers(username))


def get_case(case_id, username=DOCTOR):
    return client.get(f"/api/cases/{case_id}", headers=auth_headers(username)).json()


def get_segments(case_id, username=DOCTOR):
    return client.get(f"/api/cases/{case_id}/segments", headers=auth_headers(username)).json()


class TestReports:
    """Clips of one report become new information together, once the report is sent and transcribed."""

    def test_report_is_new_information_once_sent_and_transcribed(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "58 male, chest pain", 1: "BP 92 over 58"}))
        case = start_case()
        assert upload(case["id"], 0, report_id="r1").status_code == 201
        assert upload(case["id"], 1, report_id="r1").status_code == 201

        # Transcribed and shown, but the crew is still reporting: not new information yet.
        during = get_case(case["id"])
        assert during["info_version"] == 1
        assert during["open_report"] == {"id": "r1", "clips": 2, "started_at": during["open_report"]["started_at"]}
        segments = get_segments(case["id"])
        assert [s["text"] for s in segments] == ["58 male, chest pain", "BP 92 over 58"]
        assert [s["report_id"] for s in segments] == ["r1", "r1"]
        assert [s["report_complete"] for s in segments] == [0, 0]
        assert [s["info_version"] for s in segments] == [None, None]

        sent = end_report(case["id"], "r1", 2)
        assert sent.status_code == 200, sent.text
        assert sent.json()["completed_at"] and sent.json()["info_version"] == 2
        after = get_case(case["id"])
        assert after["info_version"] == 2
        assert after["open_report"] is None
        assert [(s["info_version"], s["report_complete"]) for s in get_segments(case["id"])] == [(2, 1), (2, 1)]

    def test_acknowledgment_holds_while_the_crew_is_reporting(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "pale", 1: "faint"}))
        case = start_case()
        client.post(f"/api/cases/{case['id']}/acknowledge", headers=auth_headers(DOCTOR))
        upload(case["id"], 0, report_id="r1")
        upload(case["id"], 1, report_id="r1")
        assert get_case(case["id"])["operational_status"] == "acknowledged"

        end_report(case["id"], "r1", 2)
        assert get_case(case["id"])["operational_status"] == "inbound"

    def test_report_sent_before_its_clips_arrive(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "first", 1: "second"}))
        case = start_case()
        sent = end_report(case["id"], "r1", 2)
        assert sent.status_code == 200
        assert sent.json()["completed_at"] is None

        upload(case["id"], 1, report_id="r1")
        assert get_case(case["id"])["info_version"] == 1
        upload(case["id"], 0, report_id="r1")
        assert get_case(case["id"])["info_version"] == 2

    def test_report_without_text_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: ""}))
        case = start_case()
        upload(case["id"], 0, report_id="r1")
        assert end_report(case["id"], "r1", 1).json()["completed_at"]
        assert get_case(case["id"])["info_version"] == 1

    def test_failed_clip_completes_its_report_and_flags_the_case(self, monkeypatch):
        def transcribe(audio_path, prompt):
            if "-seg-1-" in audio_path:
                raise RuntimeError("boom")
            return "airway clear"

        monkeypatch.setattr(cases, "transcribe_segment_sync", transcribe)
        case = start_case()
        upload(case["id"], 0, report_id="r1")
        upload(case["id"], 1, report_id="r1")
        end_report(case["id"], "r1", 2)
        after = get_case(case["id"])
        assert after["info_version"] == 2
        assert after["processing"]["needs_review"]

        # The retried clip arrives after its report completed: it is new information on its own.
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({1: "breathing 28"}))
        failed = [s for s in get_segments(case["id"]) if s["status"] == "failed"][0]
        assert client.post(f"/api/cases/{case['id']}/segments/{failed['id']}/retry",
                           headers=auth_headers(EMT)).status_code == 200
        assert get_case(case["id"])["info_version"] == 3

    @pytest.mark.parametrize("finish", ["close", "arrive"])
    def test_closing_or_arrival_ends_an_unsent_report(self, monkeypatch, finish):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "GCS 14"}))
        case = start_case()
        upload(case["id"], 0, report_id="r1")
        assert get_case(case["id"])["info_version"] == 1

        assert client.post(f"/api/cases/{case['id']}/{finish}", headers=auth_headers(EMT)).status_code == 200
        after = get_case(case["id"])
        assert after["info_version"] == 2
        assert after["open_report"] is None

    def test_restart_ends_reports_of_cases_that_closed(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "SpO2 88"}))
        case = start_case()
        upload(case["id"], 0, report_id="r1")
        database.run("UPDATE cases SET status = 'closed' WHERE id = ?", (case["id"],))

        cases.settle_reports_after_restart()
        assert get_case(case["id"])["info_version"] == 2

    def test_sending_a_report_again(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "stable"}))
        case = start_case()
        upload(case["id"], 0, report_id="r1")
        assert end_report(case["id"], "r1", 1).status_code == 200
        assert end_report(case["id"], "r1", 1).status_code == 200
        assert get_case(case["id"])["info_version"] == 2
        assert end_report(case["id"], "r1", 2).status_code == 409

    def test_report_rejections(self):
        case = start_case()
        assert end_report(case["id"], "clip:x", 1).status_code == 400
        assert end_report(case["id"], "r1", -1).status_code == 400
        assert end_report(case["id"], "r1", True).status_code == 400
        assert upload(case["id"], 0, report_id="clip:x").status_code == 400
        assert end_report(case["id"], "r1", 1, username=DOCTOR).status_code == 403
        other = start_case(username=OTHER_EMT)
        assert end_report(other["id"], "r1", 1).status_code == 404

        client.post(f"/api/cases/{case['id']}/close", headers=auth_headers(EMT))
        assert end_report(case["id"], "r2", 1).status_code == 409

    def test_ai_readers_skip_unfinished_reports(self, monkeypatch):
        import case_extraction
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "BP 92 over", 1: "58"}))
        case = start_case(patient_info=None)
        upload(case["id"], 0, report_id="r1")
        assert case_assessment.read_inputs(case["id"])["has_information"] is False
        assert case_extraction.read_new_information(case["id"])["new"] == []
        assert case_extraction.read_new_information(case["id"])["earlier"] == []

        upload(case["id"], 1, report_id="r1")
        end_report(case["id"], "r1", 2)
        assert case_assessment.read_inputs(case["id"])["input"]["current_transcript"] == "BP 92 over 58"
        assert [i["text"] for i in case_extraction.read_new_information(case["id"])["new"]] == ["BP 92 over", "58"]


class TestReportCompletionReachesScreens:
    """Screens only take clip rows from segment events, so a finished report must publish all its clips.
    From Astra's Oct 13 audit (R4): the transcript stayed "reporting..." until a reload."""

    @staticmethod
    def capture(monkeypatch):
        events = []
        original = cases.broker.publish

        def publish(kind, data, **kwargs):
            events.append((kind, copy.deepcopy(data)))
            return original(kind, data, **kwargs)
        monkeypatch.setattr(cases.broker, "publish", publish)
        return events

    @staticmethod
    def completed_rows(events):
        return {data["segment"]["id"] for kind, data in events
                if kind == "segment.updated" and data["segment"]["report_complete"] == 1}

    def test_sending_a_transcribed_report_publishes_every_clip(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "BP 100 over", 1: "64."}))
        case = start_case()
        upload(case["id"], 0, report_id="sent-last")
        upload(case["id"], 1, report_id="sent-last")
        events = self.capture(monkeypatch)
        assert end_report(case["id"], "sent-last", 2).status_code == 200
        after = get_segments(case["id"])
        assert all(s["report_complete"] == 1 and s["info_version"] == 2 for s in after)
        assert self.completed_rows(events) == {s["id"] for s in after}

    def test_the_last_transcription_publishes_every_clip_of_its_report(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "BP 100 over", 1: "64."}))
        case = start_case()
        upload(case["id"], 0, report_id="clip-last")
        assert end_report(case["id"], "clip-last", 2).status_code == 200
        events = self.capture(monkeypatch)
        upload(case["id"], 1, report_id="clip-last")
        after = get_segments(case["id"])
        assert all(s["report_complete"] == 1 and s["info_version"] == 2 for s in after)
        assert self.completed_rows(events) == {s["id"] for s in after}

    def test_closing_mid_report_publishes_its_clips(self, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "Patient stable"}))
        case = start_case()
        upload(case["id"], 0, report_id="unsent")
        events = self.capture(monkeypatch)
        client.post(f"/api/cases/{case['id']}/close", headers=auth_headers(EMT))
        [clip] = get_segments(case["id"])
        assert clip["report_complete"] == 1
        assert self.completed_rows(events) == {clip["id"]}


class TestBroker:
    @pytest.mark.parametrize("change", ["expired", "hospital", "role"])
    def test_open_stream_stops_before_delivering_after_authorization_changes(self, monkeypatch, change):
        """Exercise the production stream iterator after it has already authenticated and connected."""
        user = query("SELECT id, role, hospital_id FROM users WHERE username = ?", (DOCTOR,))[0]
        user["exp"] = time.time() + 3600
        monkeypatch.setattr(cases, "HEARTBEAT_SECONDS", 0.001)

        async def scenario():
            response = await cases.case_events(user)
            stream = response.body_iterator
            try:
                assert "retry:" in await anext(stream)
                assert "event: ready" in await anext(stream)
                if change == "expired":
                    user["exp"] = 0  # token crosses its expiry after connection
                elif change == "hospital":
                    database.run("UPDATE users SET hospital_id = NULL WHERE id = ?", (user["id"],))
                else:
                    database.run("UPDATE users SET role = 'emt' WHERE id = ?", (user["id"],))
                cases.broker.publish("case.updated", {"patient_info": "must not reach this session"},
                                     hospital_id=user["hospital_id"])
                message = await anext(stream)
                assert "event: auth.expired" in message
                assert "patient_info" not in message
                with pytest.raises(StopAsyncIteration):
                    await anext(stream)
            finally:
                await stream.aclose()
                database.run("UPDATE users SET role = ?, hospital_id = ? WHERE id = ?",
                             (user["role"], user["hospital_id"], user["id"]))

        count = cases.broker.subscriber_count
        asyncio.run(scenario())
        assert cases.broker.subscriber_count == count

    def test_events_are_filtered_by_owner(self):
        async def scenario():
            broker = EventBroker()
            doctor = broker.subscribe(lambda owner, hospital: True)
            emt = broker.subscribe(lambda owner, hospital: owner == 7)
            broker.publish("segment.created", {"n": 1}, owner_id=7)
            broker.publish("segment.created", {"n": 2}, owner_id=8)
            return doctor.queue.qsize(), emt.queue.qsize(), emt.queue.get_nowait()

        doctor_count, emt_count, message = asyncio.run(scenario())
        assert doctor_count == 2
        assert emt_count == 1
        assert 'data: {"n":1}' in message

    def test_slow_subscriber_is_dropped_with_close_signal(self, monkeypatch):
        import realtime
        monkeypatch.setattr(realtime, "MAX_QUEUED_EVENTS", 2)

        async def scenario():
            broker = EventBroker()
            sub = broker.subscribe(lambda owner, hospital: True)
            for i in range(3):
                broker.publish("e", {"i": i})
            return broker.subscriber_count, sub.queue.get_nowait()

        count, message = asyncio.run(scenario())
        assert count == 0
        assert message is None


@pytest.fixture
def live_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


class SSEReader:
    """Parses SSE frames from one streaming response across several reads."""

    def __init__(self, response):
        self.chunks = response.iter_text()
        self.buffer = ""
        self.events = []

    def read_until(self, until, timeout=10):
        deadline = time.time() + timeout
        while not until(self.events):
            assert time.time() < deadline, f"timed out; got {self.events}"
            self.buffer += next(self.chunks)
            while "\n\n" in self.buffer:
                frame, self.buffer = self.buffer.split("\n\n", 1)
                fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line and not line.startswith(":"))
                if "event" in fields:
                    self.events.append((fields["event"], json.loads(fields["data"])))
        return self.events


class TestLiveStream:
    def test_doctor_receives_segments_in_near_real_time(self, live_server, monkeypatch):
        monkeypatch.setattr(cases, "transcribe_segment_sync", fake_transcriber({0: "Patient is diaphoretic"}))
        case = start_case()

        with httpx.Client(base_url=live_server, timeout=10) as http:
            with http.stream("GET", "/api/cases/events", headers=auth_headers(DOCTOR)) as stream:
                assert stream.status_code == 200
                assert stream.headers["content-type"].startswith("text/event-stream")
                reader = SSEReader(stream)
                assert reader.read_until(lambda e: bool(e))[0][0] == "ready"

                response = http.post(
                    f"/api/cases/{case['id']}/segments",
                    files={"audio": ("segment-0.webm", b"bytes", "audio/webm")},
                    data={"seq": "0", "client_id": "clip-live", "recorded_at": "2026-01-01T15:00:00Z"},
                    headers=auth_headers(EMT),
                )
                assert response.status_code == 201

                events = reader.read_until(lambda e: any(t == "segment.updated" for t, _ in e))

        by_type = {t: d for t, d in events}
        assert by_type["case.updated"]["case"]["segment_count"] == 1
        assert by_type["segment.created"]["case_id"] == case["id"]
        assert by_type["segment.created"]["segment"]["status"] == "pending"
        assert by_type["segment.updated"]["segment"]["status"] == "completed"
        assert by_type["segment.updated"]["segment"]["text"] == "Patient is diaphoretic"

    def test_stream_requires_auth(self, live_server):
        response = httpx.get(f"{live_server}/api/cases/events")
        assert response.status_code == 401

    def test_disconnected_stream_unsubscribes(self, live_server):
        from realtime import broker

        before = broker.subscriber_count
        with httpx.Client(base_url=live_server, timeout=10) as http:
            with http.stream("GET", "/api/cases/events", headers=auth_headers(DOCTOR)) as stream:
                SSEReader(stream).read_until(lambda e: bool(e))
                assert broker.subscriber_count == before + 1
        deadline = time.time() + 5
        while broker.subscriber_count != before and time.time() < deadline:
            time.sleep(0.05)
        assert broker.subscriber_count == before
