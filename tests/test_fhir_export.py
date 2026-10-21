"""The handoff as a FHIR R4 Bundle (fhir_export.py, GET /api/cases/{id}/fhir)."""
import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-testing-only")

from main import app
from backend import case_assessment
from backend import case_extraction
from backend import database
from backend.database import init_database, insert_sample_data, query, run, utc_now_iso
from backend.middleware.auth import create_access_token

client = TestClient(app, raise_server_exceptions=False)
EMT, DOCTOR = "emt.wilson", "dr.smith"


@pytest.fixture(scope="module", autouse=True)
def temp_database(tmp_path_factory):
    previous = database.DB_PATH
    database.DB_PATH = tmp_path_factory.mktemp("fhir-db") / "test.db"
    init_database()
    insert_sample_data()
    yield
    database.DB_PATH = previous


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    async def fake_score(inputs):
        return {"summary": "Adult short of breath, SpO2 falling.", "meaningful_change": "No earlier report",
                "change_explanation": "-", "missing_information": "Temperature not given.",
                "esi_level": "2", "esi_reason": "-", "risk_score": 7, "risk_reason": "-"}, {}

    async def fake_extract(info):
        return {"chief_complaint": [], "vitals": [], "findings": [], "treatments": []}, {}

    monkeypatch.setattr(case_assessment, "score", fake_score)
    monkeypatch.setattr(case_extraction, "extract", fake_extract)
    monkeypatch.setattr(case_assessment, "SETTLE_SECONDS", 0)
    run("UPDATE cases SET status = 'closed' WHERE status = 'active'")


def auth(username):
    user = query("SELECT id, username, role FROM users WHERE username = ?", (username,))[0]
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def handoff_case():
    """A case with a typed summary, typed vitals, one radio clip and facts extracted from the clip."""
    hospital = query("SELECT id FROM hospitals WHERE code = 'GEN'")[0]["id"]
    case = client.post("/api/cases", json={"patient_info": "67F short of breath", "destination_hospital_id": hospital,
                                           "ems_unit": "Medic 12", "eta_minutes": 15}, headers=auth(EMT)).json()
    response = client.post(f"/api/cases/{case['id']}/updates", json={"kind": "vitals", "vitals": {"hr": 110}},
                           headers=auth(EMT))
    assert response.status_code == 201, response.text
    now = utc_now_iso()
    clip = "Saturation 89 percent on room air. No chest pain. Oxygen started."
    segment = run("""INSERT INTO transcript_segments (case_id, seq, client_id, recorded_at, audio_file_path, status,
                                                      text, created_at, updated_at, info_version)
                     VALUES (?, 0, 'clip-0', ?, 'x', 'completed', ?, ?, ?, 3)""", (case["id"], now, clip, now, now))["id"]
    run_id = run("""INSERT INTO extraction_runs (case_id, from_version, to_version, status, extractor_version,
                                                 started_at, updated_at)
                    VALUES (?, 0, 3, 'completed', 'test-model/extraction_v3', ?, ?)""", (case["id"], now, now))["id"]
    facts = [("chief_complaint", "short of breath", None, None, None, None),
             ("vital", "SpO2 89 % (on room air)", "on room air", "spo2", 89, "Saturation 89 percent on room air"),
             ("finding", "chest pain", "absent", None, None, "No chest pain"),
             ("treatment", "oxygen", "given", None, None, "Oxygen started")]
    for kind, value, detail, name, number, quote in facts:
        run("""INSERT INTO case_findings (case_id, run_id, segment_id, finding_type, value, detail, vital_name,
                                          vital_value, source_text, recorded_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case["id"], run_id, None if kind == "chief_complaint" else segment, kind, value, detail, name, number,
             quote or "short of breath", now, now))
    return case["id"], clip


def bundle_for(case_id, username=DOCTOR):
    response = client.get(f"/api/cases/{case_id}/fhir", headers=auth(username))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/fhir+json")
    return response.json()


def resources(bundle, kind):
    return [entry["resource"] for entry in bundle["entry"] if entry["resource"]["resourceType"] == kind]


def decoded(document):
    return base64.b64decode(document["content"][0]["attachment"]["data"]).decode("utf-8")


def test_every_reference_resolves_inside_the_bundle():
    case_id, _ = handoff_case()
    bundle = bundle_for(case_id)
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection"
    urls = {entry["fullUrl"] for entry in bundle["entry"]}
    references = []

    def walk(value):
        if isinstance(value, dict):
            if "reference" in value:
                references.append(value["reference"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(bundle)
    assert references and set(references) <= urls


def test_each_extracted_fact_points_to_the_report_it_quotes():
    case_id, clip = handoff_case()
    bundle = bundle_for(case_id)
    by_url = {entry["fullUrl"]: entry["resource"] for entry in bundle["entry"]}
    provenances = resources(bundle, "Provenance")
    quoted = [p for p in provenances if p["entity"][0]["role"] == "quotation"]
    # Chief complaint (on the Encounter), the radio vital, the finding and the treatment.
    assert len(quoted) == 4
    for provenance in quoted:
        [entity] = provenance["entity"]
        quote = entity["extension"][0]["valueString"]
        assert quote in decoded(by_url[entity["what"]["reference"]])
        assert by_url[provenance["agent"][0]["who"]["reference"]]["deviceName"][0]["name"] == \
            "AI model test-model/extraction_v3"
    targets = {by_url[p["target"][0]["reference"]]["resourceType"] for p in quoted}
    assert targets == {"Encounter", "Observation", "Procedure"}
    assert clip in [decoded(d) for d in resources(bundle, "DocumentReference")]


def test_clinical_content_is_coded_and_preliminary():
    case_id, _ = handoff_case()
    bundle = bundle_for(case_id)
    [encounter] = resources(bundle, "Encounter")
    assert encounter["status"] == "planned" and encounter["class"]["code"] == "EMER"
    assert encounter["reasonCode"] == [{"text": "short of breath"}] and encounter["period"]["start"]
    observations = resources(bundle, "Observation")
    assert {o["status"] for o in observations} == {"preliminary"}
    vitals = {o["code"]["coding"][0]["code"]: o["valueQuantity"]["value"] for o in observations
              if o["category"][0]["coding"][0]["code"] == "vital-signs"}
    assert vitals == {"8867-4": 110, "59408-5": 89}          # typed HR, spoken SpO2
    [finding] = [o for o in observations if o["category"][0]["coding"][0]["code"] == "exam"]
    assert (finding["code"]["text"], finding["valueBoolean"]) == ("chest pain", False)
    [procedure] = resources(bundle, "Procedure")
    assert (procedure["code"]["text"], procedure["status"]) == ("oxygen", "completed")


def test_the_ai_summary_is_labelled_and_the_ai_category_is_left_out():
    case_id, _ = handoff_case()
    bundle = bundle_for(case_id)
    [summary] = [d for d in resources(bundle, "DocumentReference") if d["type"]["text"].startswith("AI-drafted")]
    assert "Missing: Temperature not given." in decoded(summary)
    authored = [p for p in resources(bundle, "Provenance") if p["agent"][0]["type"]["coding"][0]["code"] == "author"]
    assert len(authored) == 1
    text = json.dumps(bundle) + "".join(decoded(d) for d in resources(bundle, "DocumentReference"))
    assert "Prepare now" not in text and "preparation" not in text.lower()


def test_same_access_as_the_case():
    case_id, _ = handoff_case()
    assert client.get(f"/api/cases/{case_id}/fhir", headers=auth(EMT)).status_code == 200
    assert client.get(f"/api/cases/{case_id}/fhir", headers=auth("emt.garcia")).status_code == 404
    assert client.get(f"/api/cases/{case_id}/fhir", headers=auth("dr.jones")).status_code == 404
    assert client.get(f"/api/cases/{case_id}/fhir").status_code == 401


def test_validates_against_the_fhir_models_when_available():
    models = pytest.importorskip("fhir.resources.R4B.bundle")
    case_id, _ = handoff_case()
    models.Bundle.parse_obj(bundle_for(case_id))


# From Astra's Oct 13 audit (R5): the summary may cite only what its assessment read, and an outdated
# summary must say so (information version alone can't show an ETA-only change).
def test_an_older_summary_does_not_cite_information_it_never_read(monkeypatch):
    case_id, _ = handoff_case()

    async def pending(_):
        pass
    monkeypatch.setattr(case_assessment.runner, "request", pending)
    response = client.post(f"/api/cases/{case_id}/updates", headers=auth(EMT),
                           json={"kind": "correction", "body": "Correction: oxygen saturation now 78 percent."})
    assert response.status_code == 201
    assert client.get(f"/api/cases/{case_id}", headers=auth(DOCTOR)).json()["assessment_is_outdated"] is True
    bundle = bundle_for(case_id)
    by_url = {entry["fullUrl"]: entry["resource"] for entry in bundle["entry"]}
    [summary_source] = [p for p in resources(bundle, "Provenance")
                        if p["agent"][0]["type"]["coding"][0]["code"] == "author"]
    claimed = [decoded(by_url[e["what"]["reference"]]) for e in summary_source["entity"]]
    assert claimed and "Correction: oxygen saturation now 78 percent." not in claimed


def test_a_summary_outdated_by_an_eta_change_says_so(monkeypatch):
    case_id, _ = handoff_case()

    async def pending(_):
        pass
    monkeypatch.setattr(case_assessment.runner, "request", pending)
    assert client.post(f"/api/cases/{case_id}/updates", headers=auth(EMT),
                       json={"kind": "eta", "eta_minutes": 1}).status_code == 201
    assert client.get(f"/api/cases/{case_id}", headers=auth(DOCTOR)).json()["assessment_is_outdated"] is True
    [summary] = [d for d in resources(bundle_for(case_id), "DocumentReference") if d["type"]["text"].startswith("AI-drafted")]
    assert summary["description"].startswith("Outdated: ")
    assert "Outdated:" in decoded(summary)
