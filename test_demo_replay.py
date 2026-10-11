"""The three-patient demo replay (KAN-6), run end to end against the app with fake AI services."""
import array
import io
import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import case_assessment
import case_extraction
import database
from database import init_database, insert_sample_data, query
from demo import pilot_scenario, replay
from routes import cases, recordings

SCENARIO_PATH = Path(replay.DEFAULT_SCENARIO)
PILOT_CASES = Path(__file__).parent / "docs" / "clinician-annotation" / "pilot_cases.json"

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("demo-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


def text_wav(text: str, seconds: float = 0) -> bytes:
    """A WAV whose samples carry `text`, padded with silence; fake_whisper reads the text back."""
    payload = text.encode()
    payload += b"\0" * (len(payload) % 2 + int(seconds * 16000) * 2)
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(payload)
    return out.getvalue()


def fake_whisper(audio_path, prompt):
    try:
        with wave.open(audio_path) as clip:
            return clip.readframes(clip.getnframes()).rstrip(b"\0").decode(errors="ignore")
    except wave.Error:
        raise openai.BadRequestError(
            "Invalid file format.", response=SimpleNamespace(request=None, status_code=400, headers={}), body=None)


async def fake_scorer(inputs):
    text = inputs["input"]["current_transcript"]
    worse = any(phrase in text for phrase in ("Still weak", "few words", "worse"))
    has_prior = inputs["input"]["prior_information"] != "No earlier report provided."
    return {"summary": f"version {inputs['version']}", "change_explanation": "-", "missing_information": "None identified",
            "meaningful_change": ("Yes" if worse else "No") if has_prior else "No earlier report",
            "preparation_category": "Prepare now" if worse else "Can wait"}, \
        {"input_tokens": 1000, "output_tokens": 200, "latency_ms": 900}


async def fake_extractor(info):
    """Every heart rate spoken in the new items, the way the model would cite them."""
    vitals = []
    for item in info["new"]:
        words = item["text"].replace(",", "").split()
        for index, word in enumerate(words[:-1]):
            if word.lower() in ("rate", "pulse") and words[index + 1].isdigit():
                vitals.append({"name": "hr", "value": int(words[index + 1]), "qualifier": "", "item": item["id"],
                               "quote": f"{word} {words[index + 1]}"})
    return {"chief_complaint": [], "vitals": vitals, "findings": [], "treatments": []}, \
        {"input_tokens": 300, "output_tokens": 80, "latency_ms": 700}


@pytest.fixture
def scenario():
    return json.loads(SCENARIO_PATH.read_text())


@pytest.fixture
def audio_dir(tmp_path, scenario):
    for patient in scenario["patients"]:
        for report, text in patient["reports"].items():
            (tmp_path / f"{patient['source_case_id']}-{report}.wav").write_bytes(text_wav(text))
    return tmp_path


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(recordings, "openai_client", None)
    monkeypatch.setattr(cases, "SEGMENT_DIR", tmp_path / "segments")
    monkeypatch.setattr(cases, "transcribe_segment_sync", fake_whisper)
    monkeypatch.setattr(case_assessment, "score", fake_scorer)
    monkeypatch.setattr(case_extraction, "extract", fake_extractor)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)


def run_replay(scenario, audio_dir, **options):
    run = replay.Replay(client, scenario, audio_dir=audio_dir, sleep=lambda seconds: None, log=lambda line: None,
                        **options)
    return run, run.run()


def by_source(results):
    return {case["source_case_id"]: case for case in results}


class TestThreePatientReplay:
    def test_full_demo_story(self, scenario, audio_dir):
        run, results = run_replay(scenario, audio_dir)
        cases_ = by_source(results)
        assert set(cases_) == {"SYN002", "SYN008", "SYN010"}
        gen = query("SELECT id FROM hospitals WHERE code = 'GEN'")[0]["id"]

        for source_case_id, case in cases_.items():
            assert case["source_run_id"] == run.run_id
            assert run.manifest()["cases"][source_case_id] == case["id"]
            # All three are inbound to the same hospital, with the latest information acknowledged.
            assert case["destination_hospital_id"] == gen
            assert case["operational_status"] == "acknowledged"
            assert case["latest_update_acknowledged"] is True
            # Each patient's condition changed and was re-assessed on the newest information,
            # compared with what the doctor had acknowledged.
            assert case["preparation_category"] == "Prepare now"
            assert case["current_assessment"]["meaningful_change"] == "Yes"
            assert case["current_assessment"]["based_on_version"] == case["info_version"]
            history = client.get(f"/api/cases/{case['id']}/assessments", headers=self.doctor()).json()
            assert [a["preparation_category"] for a in history if a["status"] == "completed"][-1] == "Can wait"

        # Audio went through the live-transcription path, in order.
        segments = client.get(f"/api/cases/{cases_['SYN002']['id']}/segments", headers=self.doctor()).json()
        assert [s["text"] for s in segments] == [scenario["patients"][1]["reports"]["initial"],
                                                 scenario["patients"][1]["reports"]["update"]]

        # The radio dropout was handled: the update arrived as text and the crew dismissed the
        # lost clip, which stays on record.
        syn008 = cases_["SYN008"]
        assert syn008["processing"]["needs_review"] is False
        failed = [s for s in client.get(f"/api/cases/{syn008['id']}/segments", headers=self.doctor()).json()
                  if s["status"] == "failed"]
        assert len(failed) == 1 and failed[0]["dismissed_by_name"] == "Jordan Lee"
        updates = client.get(f"/api/cases/{syn008['id']}/updates", headers=self.doctor()).json()
        assert [u["kind"] for u in updates] == ["note", "eta"]

        messages = client.get(f"/api/cases/{cases_['SYN010']['id']}/messages", headers=self.doctor()).json()["messages"]
        assert [(m["sender_role"], m["body"]) for m in messages] == [
            ("doctor", "Received. Trauma team is being notified."), ("emt", "Copy.")]

    def test_replay_can_run_again(self, scenario, audio_dir):
        first, _ = run_replay(scenario, audio_dir)
        second, results = run_replay(scenario, audio_dir)
        assert set(first.cases.values()).isdisjoint(second.cases.values())
        closed = query(f"SELECT status FROM cases WHERE id IN ({','.join('?' * 3)})", tuple(first.cases.values()))
        assert {row["status"] for row in closed} == {"closed"}
        assert all(case["status"] == "active" for case in results)

    def test_typed_mode_needs_no_audio(self, scenario, tmp_path):
        _, results = run_replay(scenario, tmp_path / "no-audio", typed=True)
        for case in results:
            updates = client.get(f"/api/cases/{case['id']}/updates", headers=self.doctor()).json()
            assert sum(u["kind"] == "note" for u in updates) == 2
            assert case["preparation_category"] == "Prepare now"

    def test_finish_hands_over_every_patient(self, scenario, audio_dir):
        run, _ = run_replay(scenario, audio_dir)
        run.finish()
        assert {c["operational_status"] for c in run.summary()} == {"closed"}
        assert all(c["arrived_at"] for c in run.summary())

    def test_queue_changes_are_recorded_for_every_patient(self, scenario, audio_dir):
        run, _ = run_replay(scenario, audio_dir)
        changes = run.queue_changes()
        assert {row["source_case_id"] for row in changes} == {"SYN002", "SYN008", "SYN010"}
        assert all(row["change"] == "entered" for row in changes[:1])
        final = run.queue_order()["cases"]
        last = {}
        for row in changes:
            last[row["source_case_id"]] = row["to_position"]
        # The recorded history ends where the screen's queue is.
        assert last == {c["source_case_id"]: c["queue_position"] for c in final}

    def test_final_queue_lists_every_patient_in_server_order(self, scenario, audio_dir):
        run, _ = run_replay(scenario, audio_dir)
        queue = run.queue_order()
        assert sorted(c["source_case_id"] for c in queue["cases"]) == ["SYN002", "SYN008", "SYN010"]
        assert [c["queue_position"] for c in queue["cases"]] == [1, 2, 3]

    def test_model_usage_adds_up_the_run(self, scenario, audio_dir):
        run, _ = run_replay(scenario, audio_dir)
        usage = run.model_usage()
        assert usage["assessments"] > 0 and usage["extractions"] > 0
        assert usage["input_tokens"] == 1000 * usage["assessments"] + 300 * usage["extractions"]
        assert usage["cost_usd"] is None or usage["cost_usd"] > 0

    def test_speed_measures_each_step_in_real_seconds(self, scenario, audio_dir):
        run, _ = run_replay(scenario, audio_dir)
        speed = run.speed()
        # The test recordings are one clip per radioed report: three initial reports and two updates
        # (SYN008's update is typed; its garbled clip failed). One assessment and one extraction per report.
        assert speed["transcript"]["n"] == 5
        assert speed["assessment"]["n"] >= 6 and speed["extraction"]["n"] >= 6
        assert all(0 <= value["median_s"] <= value["max_s"] < 30 for value in speed.values())

    def test_heart_rates_spoken_on_the_radio_are_extracted_into_the_trend(self, scenario, audio_dir):
        run, results = run_replay(scenario, audio_dir)
        syn010 = by_source(results)["SYN010"]
        heart_rates = client.get(f"/api/cases/{syn010['id']}/findings", headers=run._headers("dr.smith")).json()["vitals"]
        assert [(v["value"], v["source"]["kind"]) for v in heart_rates if v["name"] == "hr"] == [(106, "radio"), (132, "radio")]

    def test_replay_stops_instead_of_acting_on_pending_transcripts(self, scenario, audio_dir, monkeypatch):
        run, _ = run_replay(scenario, audio_dir)
        monkeypatch.setattr(run, "_wait_for", lambda *args, **kwargs: False)
        with pytest.raises(replay.ReplayError, match="not acknowledging"):
            run.acknowledge_all({"minute": 12})
        with pytest.raises(replay.ReplayError, match="still transcribing"):
            run.dismiss_failed({"minute": 8, "patient": "SYN008"})

    def test_missing_audio_is_reported_before_anything_runs(self, scenario, tmp_path):
        with pytest.raises(replay.ReplayError, match="make_audio.py"):
            run_replay(scenario, tmp_path)

    def test_doctor_acknowledges_after_the_transcript_arrives(self, scenario, audio_dir):
        run, _ = run_replay(scenario, audio_dir)
        case_id = run.cases["SYN002"]
        now = database.utc_now_iso()
        database.run("""INSERT INTO transcript_segments (case_id, seq, client_id, recorded_at, audio_file_path, status,
                            created_at, updated_at) VALUES (?, 99, 'late', ?, 'x', 'pending', ?, ?)""",
                     (case_id, now, now, now))

        def transcript_arrives(seconds):  # Whisper finishing while the doctor waits
            database.run("UPDATE transcript_segments SET status = 'completed', text = 'late' WHERE client_id = 'late'")
            database.run("UPDATE cases SET info_version = info_version + 1 WHERE id = ?", (case_id,))

        run.sleep = transcript_arrives
        run.acknowledge_all({"minute": 12})
        case = client.get(f"/api/cases/{case_id}", headers=self.doctor()).json()
        assert case["latest_update_acknowledged"] is True

    @staticmethod
    def doctor():
        token = client.post("/api/auth/login", json={"username": "dr.smith", "password": "password123"}).json()["token"]
        return {"Authorization": f"Bearer {token}"}


class TestScenarioFile:
    def test_events_refer_to_known_patients_and_reports(self, scenario):
        replay.validate(scenario)
        bad = {**scenario, "events": [{"minute": 0, "action": "audio_report", "patient": "SYN999", "report": "initial"}]}
        with pytest.raises(replay.ReplayError):
            replay.validate(bad)

    def test_every_emt_and_doctor_is_a_seeded_account(self, scenario):
        usernames = {p["emt"] for p in scenario["patients"]} | {scenario["doctor"]}
        rows = query(f"SELECT username, role, hospital_id FROM users WHERE username IN ({','.join('?' * len(usernames))})",
                     tuple(usernames))
        assert {r["username"] for r in rows} == usernames
        doctor = next(r for r in rows if r["username"] == scenario["doctor"])
        assert doctor["hospital_id"] == query("SELECT id FROM hospitals WHERE code = ?", (scenario["hospital_code"],))[0]["id"]
        # One open case per EMT, so each patient needs its own crew.
        assert len({p["emt"] for p in scenario["patients"]}) == len(scenario["patients"])

    @pytest.mark.skipif(not PILOT_CASES.exists(), reason="pilot_cases.json is on vrishank-branch until it is merged")
    def test_reports_match_the_pilot_dataset(self, scenario):
        pilot = {row[0]: row for row in json.loads(PILOT_CASES.read_text())}
        for patient in scenario["patients"]:
            case_id, _, update_number, elapsed, eta, prior, current = pilot[patient["source_case_id"]]
            assert patient["reports"]["update"] == current
            initial = prior.split(": ", 1)[1]
            assert patient["reports"]["initial"] == initial[0].upper() + initial[1:]
            assert patient["initial_eta_minutes"] == eta + elapsed


# Four fictional cases in the pilot dataset's shape: two single reports, two with an earlier report.
PILOT_SAMPLE = [
    ["SYN001", "E001", 1, 0, 12, "No earlier report provided.", "Adult short of breath. Heart rate 96."],
    ["SYN002", "E002", 2, 8, 20, "Eight minutes earlier: heart rate 104, awake.", "Now speaking a few words. Heart rate 126."],
    ["SYN004", "E004", 1, 0, 25, "No earlier report provided.", "New trouble speaking. Heart rate 88."],
    ["SYN007", "E007", 2, 3, 15, "Three minutes earlier: ankle pain.", "Pain worse. Pulse 101."],
]
PILOT_QUEUES = [{"queue_id": "Q01", "case_ids": ["SYN001", "SYN004", "SYN007"]},
                {"queue_id": "Q09", "case_ids": ["SYN002", "SYN999"]}]


@pytest.fixture
def pilot(tmp_path):
    path = tmp_path / "pilot_cases.json"
    path.write_text(json.dumps(PILOT_SAMPLE))
    return pilot_scenario.build(pilot_scenario.load_cases(path), [row[0] for row in PILOT_SAMPLE], PILOT_QUEUES)


class TestPilotScenario:
    def test_cases_are_replayed_as_the_dataset_describes_them(self, pilot):
        replay.validate(pilot)
        patients = {p["source_case_id"]: p for p in pilot["patients"]}
        assert patients["SYN002"]["reports"] == {"initial": "Heart rate 104, awake.",
                                                 "update": "Now speaking a few words. Heart rate 126."}
        assert patients["SYN002"]["initial_eta_minutes"] == 28 and patients["SYN002"]["patient_info"] is None
        assert patients["SYN001"]["reports"] == {"update": "Adult short of breath. Heart rate 96."}
        events = [(e["minute"], e["action"], e.get("patient")) for e in pilot["events"]]
        # Queue group Q01 runs first, together, and is recorded a minute after its last report (SYN007's
        # update, 3 minutes in). SYN002 runs after, its update 8 minutes after its acknowledged earlier report.
        assert (4, "snapshot_queue", None) in events
        start = next(m for m, action, p in events if action == "start_case" and p == "SYN002")
        assert (start + 8, "audio_report", "SYN002") in events and (start + 0.5, "acknowledge", "SYN002") in events
        # One open case per EMT: every case is finished before its EMT starts another.
        assert len({p["emt"] for p in pilot["patients"]}) == 3

    def test_the_run_exports_predictions_in_the_harness_format(self, pilot):
        run, results = run_replay(pilot, None, typed=True)
        predictions = run.predictions()
        assert predictions["kind"] == "model_predictions" and predictions["dataset_version"] == "pilot_v1"
        [config] = predictions["configs"]
        assert config["config_id"] == f"app-replay-{run.run_id}" and config["repeats"] == 1
        assert config["prompt_version"] == "awareness_v2" and config["input_contract_version"] == "app_replay_typed"
        records = {r["case_id"]: r for r in predictions["records"]}
        assert set(records) == {"SYN001", "SYN002", "SYN004", "SYN007"}
        assert all(r["status"] == "ok" and set(r["output"]) == set(case_assessment.OUTPUT_FIELDS)
                   for r in records.values())
        assert records["SYN001"]["output"]["meaningful_change"] == "No earlier report"
        assert records["SYN002"]["output"]["meaningful_change"] == "Yes"   # compared with the acknowledged report
        assert predictions["app_run"]["cases"]["SYN002"]["baseline_version"] is not None
        [queue] = predictions["queues"]
        assert queue["queue_id"] == "Q01" and sorted(queue["order"].values()) == [1, 2, 3]
        assert set(queue["order"]) == {"SYN001", "SYN004", "SYN007"}

    def test_unsure_and_failures_use_the_harness_vocabulary(self, pilot, monkeypatch):
        async def unsure_or_timeout(inputs):
            if "SYN004" in inputs["input"]["case_id"]:
                raise openai.APITimeoutError(request=None)
            return {"summary": "s", "meaningful_change": "No earlier report", "change_explanation": "-",
                    "missing_information": "Vital signs", "preparation_category": "Unsure"}, {}
        monkeypatch.setattr(case_assessment, "score", unsure_or_timeout)
        run, _ = run_replay(pilot, None, typed=True)
        records = {r["case_id"]: r for r in run.predictions()["records"]}
        assert records["SYN001"]["output"]["preparation_category"] == "Unsure"
        assert records["SYN004"]["status"] == "timeout" and "output" not in records["SYN004"]


def test_the_demo_replay_records_its_queue_exercise(scenario, audio_dir):
    run, _ = run_replay(scenario, audio_dir)
    [queue] = run.predictions()["queues"]
    assert queue["queue_id"] == "Q02" and set(queue["order"]) == {"SYN002", "SYN008", "SYN010"}


def test_cuts_land_in_pauses_not_mid_word():
    rate = 16000
    speech = array.array("h", [3000, -3000] * (rate // 2))       # 1 s of "speech"
    pause = array.array("h", [0] * (rate // 4))                  # 0.25 s of silence
    samples = speech * 6 + pause + speech * 5                    # pause at 6.0-6.25 s
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(samples.tobytes())
    first, second = replay.split_wav(out.getvalue())
    assert 6000 <= replay.clip_duration_ms(first) <= 6250


def test_high_bandwidth_wavs_are_cut_under_the_upload_limit():
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(2)
        clip.setsampwidth(4)
        clip.setframerate(192000)
        clip.writeframes(b"\0" * 192000 * 8 * 2 * 4)  # 8 s, ~12 MB
    chunks = replay.split_wav(out.getvalue())
    assert len(chunks) == 2 and all(len(c) <= replay.MAX_CLIP_BYTES + 44 for c in chunks)


def test_long_reports_are_cut_into_even_recorder_sized_segments():
    durations = [replay.clip_duration_ms(c) / 1000 for c in replay.split_wav(text_wav("x", seconds=19))]
    assert len(durations) == 3 and all(5 < d <= 8 for d in durations)  # no short tail
    assert sum(durations) == pytest.approx(19, abs=0.01)
    assert len(replay.split_wav(text_wav("x", seconds=7))) == 1
