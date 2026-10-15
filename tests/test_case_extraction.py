"""Patient information extraction (KAN-8): its own step, traceable to the clip or note each fact came from."""
import asyncio
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
import case_assessment
import case_extraction
import database
from database import init_database, insert_sample_data, query, run
from middleware.auth import create_access_token
import openai_config
from routes import cases

client = TestClient(app, raise_server_exceptions=False)

EMT = "emt.wilson"
DOCTOR = "dr.smith"          # General Hospital
OTHER_DOCTOR = "dr.jones"    # Northside
USAGE = {"input_tokens": 400, "output_tokens": 120, "latency_ms": 900}
SCORED = {"summary": "s", "meaningful_change": "No earlier report", "change_explanation": "-",
          "missing_information": "None identified", "preparation_category": "Can wait"}


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("extraction-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(openai_config, "openai_client", None)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)

    async def fake_score(inputs):
        return SCORED, {}
    monkeypatch.setattr(case_assessment, "score", fake_score)


def auth(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def use_extractor(monkeypatch, *results):
    """Each run answers with the next result (a function of the run's info, or an Exception to raise)."""
    calls = []
    remaining = list(results)

    async def fake_extract(info):
        calls.append(info)
        result = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(result, BaseException):
            raise result
        return result(info), USAGE

    monkeypatch.setattr(case_extraction, "extract", fake_extract)
    return calls


def answer(chief=(), vitals=(), findings=(), treatments=()):
    """A model answer whose entries cite the run's new items by position: (index, ...)."""
    def build(info):
        ids = [item["id"] for item in info["new"]]
        quotes = [item["text"] for item in info["new"]]
        return {
            "chief_complaint": [{"text": t, "item": ids[i], "quote": quotes[i]} for i, t in chief],
            "vitals": [{"name": n, "value": v, "qualifier": q, "item": ids[i], "quote": quotes[i]}
                       for i, n, v, q in vitals],
            "findings": [{"text": t, "status": s, "item": ids[i], "quote": quotes[i]} for i, t, s in findings],
            "treatments": [{"text": t, "status": s, "item": ids[i], "quote": quotes[i]} for i, t, s in treatments],
        }
    return build


def start_case(username=EMT, info="Adult with breathing trouble", code="GEN"):
    run("UPDATE cases SET status = 'closed' WHERE status = 'active' AND emt_id = (SELECT id FROM users WHERE username = ?)",
        (username,))
    hospital = query("SELECT id FROM hospitals WHERE code = ?", (code,))[0]["id"]
    response = client.post("/api/cases", json={"patient_info": info, "destination_hospital_id": hospital,
                                               "eta_minutes": 20}, headers=auth(username))
    assert response.status_code == 201, response.text
    return response.json()


def findings(case_id, username=DOCTOR):
    response = client.get(f"/api/cases/{case_id}/findings", headers=auth(username))
    assert response.status_code == 200, response.text
    return response.json()


def add_note(case_id, body, username=EMT):
    response = client.post(f"/api/cases/{case_id}/updates", json={"kind": "note", "body": body}, headers=auth(username))
    assert response.status_code == 201, response.text
    return response.json()


def radio(case_id, seq, text, recorded_at):
    """A clip transcribed the way routes/cases.py does it."""
    now = database.utc_now_iso()
    segment = run("""INSERT INTO transcript_segments (case_id, seq, client_id, recorded_at, audio_file_path, status,
                                                      created_at, updated_at)
                     VALUES (?, ?, ?, ?, 'x.webm', 'pending', ?, ?)""",
                  (case_id, seq, f"clip-{seq}", recorded_at, now, now))["id"]

    async def transcription(_segment):
        return text, None

    original = cases.run_transcription
    cases.run_transcription = transcription
    try:
        asyncio.run(cases.transcribe_segment(segment))
    finally:
        cases.run_transcription = original
    return segment


class TestInterpret:
    INFO = {"new": [
        {"id": "radio-7", "text": "sat 89 percent. BP 100 over 64. pale.",
         "segment_id": 7, "update_id": None, "time": "2026-10-11T18:00:00.000Z"},
        {"id": "note-3", "text": "no external bleeding; fluids are planned.",
         "segment_id": None, "update_id": 3, "time": "2026-10-11T18:01:00.000Z"},
    ]}

    def output(self, **lists):
        return {"chief_complaint": [], "vitals": [], "findings": [], "treatments": [], **lists}

    def test_keeps_traceable_facts_with_their_source(self):
        facts = case_extraction.interpret(self.output(
            vitals=[{"name": "spo2", "value": 89, "qualifier": "on room air", "item": "radio-7", "quote": "sat 89 percent"},
                    {"name": "sbp", "value": 100, "qualifier": "", "item": "radio-7", "quote": "BP 100 over 64"}],
            findings=[{"text": "External bleeding", "status": "absent", "item": "note-3", "quote": "no external bleeding"}],
            treatments=[{"text": "IV fluids", "status": "planned", "item": "note-3", "quote": "fluids are planned"}],
        ), self.INFO)
        spo2, sbp, bleeding, fluids = facts
        assert spo2["value"] == "SpO2 89 % (on room air)" and spo2["vital_value"] == 89
        assert spo2["segment_id"] == 7 and spo2["recorded_at"] == "2026-10-11T18:00:00.000Z"
        assert spo2["source_text"] == "sat 89 percent"
        assert sbp["detail"] is None
        assert (bleeding["detail"], bleeding["update_id"], bleeding["segment_id"]) == ("absent", 3, None)
        assert fluids["finding_type"] == "treatment" and fluids["detail"] == "planned"

    def test_drops_what_cant_be_traced_or_is_out_of_range(self):
        facts = case_extraction.interpret(self.output(
            vitals=[{"name": "hr", "value": 900, "qualifier": "", "item": "radio-7", "quote": "x"},
                    {"name": "hr", "value": 120, "qualifier": "", "item": "radio-99", "quote": "x"},
                    {"name": "hr", "value": True, "qualifier": "", "item": "radio-7", "quote": "x"},
                    {"name": "weight", "value": 80, "qualifier": "", "item": "radio-7", "quote": "x"}],
            findings=[{"text": "  ", "status": "present", "item": "radio-7", "quote": "x"},
                      {"text": "Pale", "status": "maybe", "item": "radio-7", "quote": "x"},
                      {"text": "Pale", "status": "present", "item": "radio-7", "quote": "pale"},
                      {"text": "pale", "status": "present", "item": "radio-7", "quote": "pale"}],
        ), self.INFO)
        assert [(f["finding_type"], f["value"]) for f in facts] == [("finding", "Pale")]

    def test_wrong_shape_is_unreadable_not_empty(self):
        assert case_extraction.interpret(None, self.INFO) is None
        assert case_extraction.interpret({"vitals": []}, self.INFO) is None
        assert case_extraction.interpret(self.output(vitals="hr 120"), self.INFO) is None
        assert case_extraction.interpret(self.output(), self.INFO) == []

    def test_schema_is_valid_for_strict_structured_output(self):
        def check(schema):
            if schema.get("type") == "object":
                assert schema["additionalProperties"] is False
                assert set(schema["required"]) == set(schema["properties"])
                for child in schema["properties"].values():
                    check(child)
            if schema.get("type") == "array":
                check(schema["items"])
        check(case_extraction.OUTPUT_SCHEMA)

    @pytest.mark.parametrize("quote", ["", None, "Heart rate 70", "Pulse 12"])
    def test_a_fact_with_an_unverified_quote_is_not_accepted(self, quote):
        info = {"new": [{"id": "radio-1", "text": "Pulse 120. No external bleeding.",
                         "segment_id": 1, "update_id": None, "time": "2026-10-11T18:00:00.000Z"}]}
        output = self.output(vitals=[{"name": "hr", "value": 120, "qualifier": "",
                                    "item": "radio-1", "quote": quote}])
        with pytest.raises(ValueError, match="source quote"):
            case_extraction.interpret(output, info)

    def test_a_quote_must_match_its_own_item_and_not_another_report(self):
        info = {"new": [
            {"id": "radio-1", "text": "Pulse 120.", "segment_id": 1, "update_id": None, "time": "t1"},
            {"id": "radio-2", "text": "No external bleeding.", "segment_id": 2, "update_id": None, "time": "t2"},
        ]}
        output = self.output(findings=[{"text": "External bleeding", "status": "absent",
                                      "item": "radio-1", "quote": "No external bleeding"}])
        with pytest.raises(ValueError, match="source quote"):
            case_extraction.interpret(output, info)

    def test_matching_normalizes_spacing_and_case_but_stores_original_words(self):
        info = {"new": [{"id": "radio-1", "text": "Pulse   120. No external bleeding.",
                         "segment_id": 1, "update_id": None, "time": "t1"}]}
        output = self.output(vitals=[{"name": "hr", "value": 120, "qualifier": "",
                                    "item": "radio-1", "quote": "pulse 120"}])
        [fact] = case_extraction.interpret(output, info)
        assert fact["source_text"] == "Pulse   120"


class TestQuoteFormatting:
    @pytest.mark.parametrize("quote, source, expected", [
        ("BP 100 over 64,", "BP 100 over 64.", "BP 100 over 64"),
        ("patient's awake", "The patient’s awake.", "patient’s awake"),
        ("Pale, cool; clammy!", "Pale. Cool, clammy.", "Pale. Cool, clammy"),
        ("“no bleeding”", "No bleeding.", "No bleeding"),
        ("temp 37.5, BP 120/80", "Temp 37.5; BP 120/80.", "Temp 37.5; BP 120/80"),
        ("change -5", "Change −5.", "Change −5"),
    ])
    def test_typographic_punctuation_keeps_the_original_source(self, quote, source, expected):
        assert case_extraction.verified_quote(quote, source) == expected

    @pytest.mark.parametrize("quote, source", [
        ("temp 375", "temp 37.5"), ("dose 5", "dose .5"),
        ("change 5", "change -5"), ("change -5", "change 5"),
        ("pulse 12", "pulse 120"), ("BP 12080", "BP 120/80"),
        ("BP 120 80", "BP 120/80"), ("value 90", "value >90"),
        ("value >90", "value >=90"), ("value 5", "value +5"),
        ("5", "- 5"), ("90", ">= 90"),
        ("no bleeding", "no external bleeding"), ("no bleeding", "bleeding not assessed"),
        ("patient is awake", "patient isn't awake"), ("...", "anything"),
    ])
    def test_matching_does_not_erase_meaningful_tokens(self, quote, source):
        with pytest.raises(case_extraction.UnverifiedSourceQuote):
            case_extraction.verified_quote(quote, source)


class TestExtractionRuns:
    def test_a_later_misquote_preserves_previously_extracted_information(self, monkeypatch):
        def invalid(info):
            result = answer(findings=[(0, "Pale", "present")])(info)
            result["findings"][0]["quote"] = "No external bleeding"
            return result
        use_extractor(monkeypatch, answer(chief=[(0, "Breathing trouble")]), invalid)
        case = start_case()
        original = findings(case["id"])["chief_complaint"]
        add_note(case["id"], "Patient is pale.")
        after = findings(case["id"])
        assert after["status"] == "failed" and after["can_retry"]
        assert after["chief_complaint"] == original
        assert after["findings"] == []

    def test_unverified_quote_fails_visibly_without_storing_partial_facts(self, monkeypatch):
        def invalid(info):
            output = answer(chief=[(0, "Breathing trouble")])(info)
            output["findings"] = [{"text": "Bleeding", "status": "absent", "item": "case-opened",
                                   "quote": "No bleeding"}]
            return output
        use_extractor(monkeypatch, invalid, answer(chief=[(0, "Breathing trouble")]))
        case = start_case()
        result = findings(case["id"])
        assert result["status"] == "failed" and result["can_retry"]
        assert "source quote" in result["error"]
        assert result["chief_complaint"] is None and result["findings"] == []
        assert client.post(f"/api/cases/{case['id']}/findings/retry", headers=auth(DOCTOR)).status_code == 202
        assert findings(case["id"])["status"] == "completed"

    def test_each_run_reads_only_new_information_with_earlier_text_as_context(self, monkeypatch):
        calls = use_extractor(
            monkeypatch,
            answer(chief=[(0, "Breathing trouble")]),
            answer(vitals=[(0, "spo2", 89, "on room air"), (0, "hr", 126, "")],
                   findings=[(0, "Speaking a few words at a time", "present")],
                   treatments=[(0, "Oxygen", "given")]),
        )
        case = start_case()
        update = add_note(case["id"], "Now speaking a few words at a time. HR 126, sat 89 percent on room air. Oxygen started.")

        assert [[i["id"] for i in call["new"]] for call in calls] == [["case-opened"], [f"note-{update['id']}"]]
        second = case_extraction.model_input(calls[1])
        assert second["earlier_text"] == "[typed when the case was opened] Adult with breathing trouble"
        assert second["new_items"][0]["source"] == "typed note"

        result = findings(case["id"])
        assert result["status"] == "completed" and result["chief_complaint"]["text"] == "Breathing trouble"
        assert result["chief_complaint"]["source"]["kind"] == "case opened"
        assert result["chief_complaint"]["source_version"] == result["chief_complaint"]["first_stated_version"] == 1
        assert {f["source_version"] for f in result["vitals"]} == {update["info_version"]}
        spo2 = next(v for v in result["vitals"] if v["name"] == "spo2")
        assert (spo2["value"], spo2["unit"], spo2["qualifier"]) == (89, "%", "on room air")
        assert spo2["source"] == {"kind": "typed", "segment_id": None, "update_id": update["id"]}
        assert result["treatments"][0]["status"] == "given"

        runs = client.get(f"/api/cases/{case['id']}/extractions", headers=auth(DOCTOR)).json()
        assert [(r["from_version"], r["to_version"], r["fact_count"]) for r in runs] == [(1, 2, 4), (0, 1, 1)]
        assert runs[0]["input_tokens"] == 400 and runs[0]["cost_usd"] is not None
        assert runs[0]["extractor_version"] == case_extraction.extractor_version()

    def test_a_repeated_complaint_keeps_when_it_was_first_given(self, monkeypatch):
        use_extractor(monkeypatch, answer(chief=[(0, "Breathing trouble")]), answer(chief=[(0, "breathing trouble")]))
        case = start_case()
        update = add_note(case["id"], "Update on our adult with breathing trouble.")
        complaint = findings(case["id"])["chief_complaint"]
        assert complaint["source_version"] == update["info_version"] and complaint["first_stated_version"] == 1

    def test_radio_facts_point_at_their_clip_and_when_it_was_spoken(self, monkeypatch):
        use_extractor(monkeypatch, answer(), answer(vitals=[(0, "hr", 132, "")]))
        case = start_case()
        segment = radio(case["id"], 0, "Pulse 132, BP 90 over 60.", "2026-10-11T18:05:00.000Z")
        hr = findings(case["id"])["vitals"][0]
        assert hr["source"]["kind"] == "radio" and hr["source"]["segment_id"] == segment
        assert hr["source_version"] == query("SELECT info_version FROM transcript_segments WHERE id = ?",
                                             (segment,))[0]["info_version"]
        assert hr["recorded_at"] == "2026-10-11T18:05:00.000Z"

    def test_a_failure_is_visible_retryable_and_leaves_the_assessment_alone(self, monkeypatch):
        calls = use_extractor(monkeypatch, RuntimeError("outage"), answer(chief=[(0, "Breathing trouble")]))
        case = start_case()
        result = findings(case["id"])
        assert result["status"] == "failed" and result["can_retry"] and result["error"] == "unexpected error"
        assert client.get(f"/api/cases/{case['id']}", headers=auth(DOCTOR)).json()["processing"]["status"] == "completed"

        response = client.post(f"/api/cases/{case['id']}/findings/retry", headers=auth(DOCTOR))
        assert response.status_code == 202
        assert findings(case["id"])["chief_complaint"]["text"] == "Breathing trouble"
        assert [[i["id"] for i in call["new"]] for call in calls] == [["case-opened"], ["case-opened"]]
        assert client.post(f"/api/cases/{case['id']}/findings/retry", headers=auth(DOCTOR)).status_code == 409

    def test_failed_information_is_read_again_with_the_next_report(self, monkeypatch):
        calls = use_extractor(monkeypatch, RuntimeError("outage"), answer())
        case = start_case()
        update = add_note(case["id"], "HR 120")
        assert [i["id"] for i in calls[-1]["new"]] == ["case-opened", f"note-{update['id']}"]
        assert findings(case["id"])["status"] == "completed"

    def test_an_unreadable_answer_fails_the_run(self, monkeypatch):
        use_extractor(monkeypatch, lambda info: {"vitals": "HR 120"})
        case = start_case()
        assert findings(case["id"])["error"] == case_assessment.UNREADABLE_ERROR

    def test_eta_changes_and_typed_vitals_are_not_sent_for_extraction(self, monkeypatch):
        calls = use_extractor(monkeypatch, answer())
        case = start_case()
        client.post(f"/api/cases/{case['id']}/updates", json={"kind": "eta", "eta_minutes": 5}, headers=auth(EMT))
        client.post(f"/api/cases/{case['id']}/updates", json={"kind": "vitals", "vitals": {"hr": 110}}, headers=auth(EMT))
        assert len(calls) == 1

    def test_only_the_cases_own_emt_and_hospital_see_it(self, monkeypatch):
        use_extractor(monkeypatch, answer(chief=[(0, "Breathing trouble")]))
        case = start_case()
        assert findings(case["id"], EMT)["chief_complaint"]["text"] == "Breathing trouble"
        for username in (OTHER_DOCTOR, "emt.garcia"):
            for path in ("findings", "extractions"):
                assert client.get(f"/api/cases/{case['id']}/{path}", headers=auth(username)).status_code == 404
            assert client.post(f"/api/cases/{case['id']}/findings/retry", headers=auth(username)).status_code == 404

    def test_snapshots_carry_an_increasing_as_of(self, monkeypatch):
        use_extractor(monkeypatch, answer())
        case = start_case()
        first, second = findings(case["id"]), findings(case["id"])
        assert first["as_of"] < second["as_of"]

    def test_changes_are_pushed_to_the_cases_viewers(self, monkeypatch):
        use_extractor(monkeypatch, answer(chief=[(0, "Breathing trouble")]))
        published = []
        monkeypatch.setattr(cases.broker, "publish",
                            lambda event, data, owner_id, hospital_id: published.append((event, data)))
        case = start_case()
        pushed = [data for event, data in published if event == "findings.updated"]
        assert [p["findings"]["status"] for p in pushed] == ["processing", "completed"]
        assert pushed[-1]["case_id"] == case["id"]


class TestRecovery:
    def test_interrupted_runs_fail_and_the_case_is_extracted_again(self, monkeypatch):
        use_extractor(monkeypatch, answer())
        case = start_case()
        add_note(case["id"], "HR 120")
        run("UPDATE extraction_runs SET status = 'processing' WHERE case_id = ? AND to_version = 2", (case["id"],))
        case_extraction.fail_interrupted_extractions()
        latest = case_extraction.latest_run(case["id"])
        assert (latest["status"], latest["error"]) == ("failed", "interrupted by a server restart")
        assert case["id"] in case_extraction.unextracted_open_cases()

    def test_the_reserved_findings_table_gains_its_columns(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "old.db")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE case_findings (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL,
                        segment_id INTEGER, finding_type TEXT NOT NULL, value TEXT NOT NULL, source_text TEXT,
                        created_at TEXT NOT NULL)""")
        database.migrate_existing_tables(conn)
        assert {"run_id", "update_id", "detail", "vital_name", "vital_value", "recorded_at"} \
            <= database.table_columns(conn, "case_findings")
