"""The verified handoff as a FHIR R4 Bundle, for the hospital's own record system.

What goes in is what the crew said or typed, and what was extracted from it with the words it
came from:
- Patient (no identity: the crew doesn't know it yet) and a planned emergency Encounter with the
  chief complaint and the expected arrival;
- each report as a DocumentReference with its text (radio clip transcript, typed note);
- vitals (LOINC-coded), findings and treatments, each "preliminary" (EMS-reported, not verified);
- for every AI-extracted fact, a Provenance: the extraction model as assembler, the crew member
  as informant, and the report it quotes (entity role "quotation"), with the exact quote;
- the AI-written summary, change since the hospital's acknowledgment and missing items, as a
  DocumentReference labelled AI-drafted, with its own Provenance naming the model version.

Deliberately left out: the AI's suggested ESI level, risk score and preparation category. The record gets facts with their sources,
not an AI urgency label (see case_queue.py and FDA's 2026 CDS guidance). Free text in the Bundle
is fine: it is the clinical record, unlike the SAS exports.

Mapped to the elements the research lists for Epic's ED pre-arrival playbook (expected encounter,
chief complaint, vitals, documents, ETA); not tested against an Epic system.
"""
import base64
import uuid

from backend.case_assessment import VITAL_SIGNS
from backend.database import query, utc_now_iso
from backend import case_extraction

SYSTEM = "https://github.com/Ishaan1402/EMS_Comms"
QUOTE_EXTENSION = f"{SYSTEM}/fhir/StructureDefinition/source-quote"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
PARTICIPANT_TYPE = "http://terminology.hl7.org/CodeSystem/provenance-participant-type"

# name -> (LOINC code, display, UCUM unit). Blood pressure is filed as its two components.
VITAL_CODES = {
    "hr": ("8867-4", "Heart rate", "/min"),
    "sbp": ("8480-6", "Systolic blood pressure", "mm[Hg]"),
    "dbp": ("8462-4", "Diastolic blood pressure", "mm[Hg]"),
    "rr": ("9279-1", "Respiratory rate", "/min"),
    "spo2": ("59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
    "temp_c": ("8310-5", "Body temperature", "Cel"),
    "gcs": ("9269-2", "Glasgow coma score total", "{score}"),
    "glucose": ("2339-0", "Glucose [Mass/volume] in Blood", "mg/dL"),
}


class _Bundle:
    def __init__(self):
        self.entries = []

    def add(self, resource: dict) -> str:
        full_url = f"urn:uuid:{uuid.uuid4()}"
        resource = {"id": full_url.rsplit(":", 1)[1], **resource}
        self.entries.append({"fullUrl": full_url, "resource": resource})
        return full_url


def _text(value: str) -> dict:
    return {"contentType": "text/plain", "data": base64.b64encode(value.encode("utf-8")).decode("ascii")}


def _ref(url: str, display: str = None) -> dict:
    return {"reference": url, **({"display": display} if display else {})}


def build(case: dict) -> dict:
    """The Bundle for one case (as routes.cases.get_case describes it), at its current information version."""
    bundle = _Bundle()
    case_id = case["id"]
    patient = bundle.add({"resourceType": "Patient", "active": True})
    hospital = bundle.add({"resourceType": "Organization", "name": case["destination_hospital_name"] or "Not set"})
    crew_name = f"{case['emt_first_name']} {case['emt_last_name']}"
    crew = bundle.add({"resourceType": "Practitioner", "name": [{"text": crew_name}]})
    findings = case_extraction.case_findings(case_id)

    encounter = {
        "resourceType": "Encounter",
        "identifier": [{"system": f"{SYSTEM}/case", "value": str(case_id)}],
        "status": "finished" if case["status"] == "closed" else "arrived" if case["arrived_at"] else "planned",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "EMER", "display": "emergency"},
        "subject": _ref(patient),
        "serviceProvider": _ref(hospital),
    }
    if findings["chief_complaint"]:
        encounter["reasonCode"] = [{"text": findings["chief_complaint"]["text"]}]
    arrival = case["arrived_at"] or case["eta_at"]
    if arrival:
        encounter["period"] = {"start": arrival}  # expected arrival while planned
    if case["ems_unit"]:
        encounter["hospitalization"] = {"origin": {"display": case["ems_unit"]}}
    encounter_url = bundle.add(encounter)
    context = {"subject": _ref(patient), "encounter": _ref(encounter_url)}

    # Every report, as the crew gave it, with the information version it became (None: not sent yet).
    sources, versions = {}, {}

    def report(key, title, text, at, version):
        versions[key] = version
        sources[key] = bundle.add({
            "resourceType": "DocumentReference", "status": "current", "docStatus": "preliminary",
            "type": {"text": title}, "subject": _ref(patient), "date": at, "author": [_ref(crew, crew_name)],
            "description": title, "content": [{"attachment": {**_text(text), "title": title}}],
            "context": {"encounter": [_ref(encounter_url)]},
        })

    if case["patient_info"]:
        report(("opened", None), "EMS summary typed when the case was opened", case["patient_info"], case["started_at"], 1)
    for update in query("""SELECT id, kind, body, created_at, info_version FROM case_updates
                           WHERE case_id = ? AND kind IN ('note', 'correction') AND body IS NOT NULL ORDER BY id""",
                        (case_id,)):
        title = "EMS correction typed by the crew" if update["kind"] == "correction" else "EMS note typed by the crew"
        report(("update", update["id"]), title, update["body"], update["created_at"], update["info_version"])
    for segment in query("""SELECT id, seq, text, recorded_at, info_version FROM transcript_segments
                            WHERE case_id = ? AND is_preview = 0 AND status = 'completed' AND text != '' ORDER BY seq""", (case_id,)):
        report(("segment", segment["id"]), f"EMS radio report transcript, clip {segment['seq'] + 1}",
               segment["text"], segment["recorded_at"], segment["info_version"])

    devices = {}

    def device(version):
        if version not in devices:
            devices[version] = bundle.add({"resourceType": "Device", "deviceName": [
                {"name": f"AI model {version}", "type": "model-name"}]})
        return devices[version]

    extractor = {row["id"]: row["extractor_version"] for row in query(
        """SELECT f.id, r.extractor_version FROM case_findings f JOIN extraction_runs r ON r.id = f.run_id
           WHERE f.case_id = ?""", (case_id,))}

    def provenance(target, fact):
        source = fact["source"]
        key = ("segment", source["segment_id"]) if source["segment_id"] else \
            ("update", source["update_id"]) if source["update_id"] else ("opened", None)
        bundle.add({
            "resourceType": "Provenance", "target": [_ref(target)], "recorded": fact["recorded_at"] or utc_now_iso(),
            "activity": {"text": "Extracted by AI from the crew's report; the quote was checked against that report"},
            "agent": [
                {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "assembler"}]},
                 "who": _ref(device(extractor.get(fact["id"], "unknown")))},
                {"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "informant"}]}, "who": _ref(crew, crew_name)},
            ],
            "entity": [{"role": "quotation", "what": _ref(sources[key]),
                        "extension": [{"url": QUOTE_EXTENSION, "valueString": fact["quote"]}]}],
        })

    def vital(name, value, at, fact=None):
        code, display, unit = VITAL_CODES[name]
        observation = {
            "resourceType": "Observation", "status": "preliminary",
            "category": [{"coding": [{"system": OBSERVATION_CATEGORY, "code": "vital-signs"}]}],
            "code": {"coding": [{"system": LOINC, "code": code, "display": display}], "text": VITAL_SIGNS[name][0]},
            **context, "effectiveDateTime": at, "performer": [_ref(crew, crew_name)],
            "valueQuantity": {"value": value, "unit": VITAL_SIGNS[name][1] or unit, "system": UCUM, "code": unit},
        }
        if fact:
            if fact["qualifier"]:
                observation["note"] = [{"text": f"Condition: {fact['qualifier']}"}]
            observation.setdefault("note", []).append({"text": f"Crew's words: “{fact['quote']}”"})
        return bundle.add(observation)

    # Typed vitals came straight from the crew; spoken ones were extracted and carry their quote.
    for reading in query("SELECT name, value, measured_at FROM vital_readings WHERE case_id = ? ORDER BY id", (case_id,)):
        vital(reading["name"], reading["value"], reading["measured_at"])
    for fact in findings["vitals"]:
        provenance(vital(fact["name"], fact["value"], fact["recorded_at"], fact), fact)

    for fact in findings["findings"]:
        observation = {
            "resourceType": "Observation", "status": "preliminary",
            "category": [{"coding": [{"system": OBSERVATION_CATEGORY, "code": "exam"}]}],
            "code": {"text": fact["text"]}, **context, "effectiveDateTime": fact["recorded_at"],
            "performer": [_ref(crew, crew_name)], "note": [{"text": f"Crew's words: “{fact['quote']}”"}],
        }
        if fact["status"] == "not assessed":
            observation["dataAbsentReason"] = {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason", "code": "not-performed"}]}
        else:
            observation["valueBoolean"] = fact["status"] != "absent"
        provenance(bundle.add(observation), fact)

    for fact in findings["treatments"]:
        procedure = {
            "resourceType": "Procedure", "status": "completed" if fact["status"] == "given" else "preparation",
            "code": {"text": fact["text"]}, **context, "performer": [{"actor": _ref(crew, crew_name)}],
            "note": [{"text": f"Crew's words: “{fact['quote']}”"}],
        }
        if fact["status"] == "given":
            procedure["performedDateTime"] = fact["recorded_at"]
        provenance(bundle.add(procedure), fact)

    if findings["chief_complaint"]:
        provenance(encounter_url, findings["chief_complaint"])

    assessment = case["current_assessment"]
    if assessment and assessment["summary"]:
        lines = [f"Summary: {assessment['summary']}"]
        if assessment["meaningful_change"] not in (None, "No earlier report"):
            lines.append(f"Change since the hospital's last acknowledgment ({assessment['meaningful_change']}): "
                         f"{assessment['change_explanation']}")
        lines.append(f"Missing: {assessment['missing_information']}")
        # The app's freshness warning travels with the summary: newer information or an ETA change since.
        outdated = case["assessment_is_outdated"]
        if outdated:
            lines.append("Outdated: written before the latest information or ETA change; "
                         "a newer AI summary is pending or failed. Check the crew's reports.")
        title = "AI-drafted EMS handoff summary (not clinician-reviewed)"
        summary = bundle.add({
            "resourceType": "DocumentReference", "status": "current", "docStatus": "preliminary",
            "type": {"text": title}, "subject": _ref(patient), "date": assessment["completed_at"],
            "description": f"{'Outdated: ' if outdated else ''}{title}, based on information version "
                           f"{assessment['based_on_version']} and the ETA known then ({assessment['input_eta_at'] or 'none'})",
            "content": [{"attachment": {**_text("\n".join(lines)), "title": title}}],
            "context": {"encounter": [_ref(encounter_url)]},
        })
        bundle.add({
            "resourceType": "Provenance", "target": [_ref(summary)], "recorded": assessment["completed_at"],
            "activity": {"text": "Written by AI from the crew's reports"},
            "agent": [{"type": {"coding": [{"system": PARTICIPANT_TYPE, "code": "author"}]},
                       "who": _ref(device(assessment["scorer_version"]))}],
            # Only what the model read: reports that were information by the version it was based on.
            "entity": [{"role": "source", "what": _ref(url)} for key, url in sources.items()
                       if versions[key] is not None and versions[key] <= assessment["based_on_version"]] or None,
        })
        if bundle.entries[-1]["resource"]["entity"] is None:
            del bundle.entries[-1]["resource"]["entity"]

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "identifier": {"system": f"{SYSTEM}/handoff", "value": f"case-{case_id}-v{case['info_version']}"},
        "type": "collection",
        "timestamp": utc_now_iso(),
        "entry": bundle.entries,
    }
