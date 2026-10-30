"""Small, provider-independent contracts and explicit missing-data rules."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "docs/clinician-annotation/pilot_cases.json"
CHANGE = ("Yes", "No", "Unclear", "No earlier report")
PREPARATION = ("Prepare now", "Can wait", "Routine", "Unsure")
STATUSES = ("Not started", "Done", "Question for team")
CASE_FIELDS = ("case_id", "encounter_id", "update_number", "elapsed_minutes",
               "eta_minutes", "prior_information", "current_transcript")
OUTPUT_FIELDS = ("summary", "meaningful_change", "change_explanation",
                 "missing_information", "preparation_category")
# awareness_v4 and later suggest an ESI level and a risk score in place of the category.
ESI = ("1", "2", "3", "4", "5", "Cannot assess")
RISK_SCORES = range(1, 11)
ESI_OUTPUT_FIELDS = ("summary", "meaningful_change", "change_explanation", "missing_information",
                     "esi_level", "esi_reason", "risk_score", "risk_reason")
ESI_PROMPT_VERSIONS = ("awareness_v4",)
# The project's own mapping from ESI to the earlier category, so category metrics and references
# still apply to ESI outputs. Not clinically validated.
PREPARATION_FOR_ESI = {"1": "Prepare now", "2": "Prepare now", "3": "Can wait", "4": "Routine", "5": "Routine",
                       "Cannot assess": "Unsure"}


def output_fields(prompt_version=None):
    return ESI_OUTPUT_FIELDS if prompt_version in ESI_PROMPT_VERSIONS else OUTPUT_FIELDS


def preparation_of(output):
    """The output's category: its own, or the one its ESI maps to. None when it has neither."""
    if "esi_level" in output:
        return PREPARATION_FOR_ESI.get(output["esi_level"])
    return output.get("preparation_category")


def load_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=unique_keys)


def text(value):
    return "" if value is None else str(value).strip()


def number(value, *, integer=False, minimum=0):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= minimum
            and (not integer or int(value) == value))


def case_dataset_version(cases):
    """Read an explicit fixture version, leaving legacy unversioned case lists compatible."""
    versions = [case.get("dataset_version") for case in cases]
    if not any(version is not None for version in versions):
        return None
    if any(not isinstance(version, str) or not version.strip() for version in versions):
        raise ValueError("Every case must declare the same nonempty dataset_version")
    if len(set(versions)) != 1:
        raise ValueError("Case dataset versions differ")
    return versions[0]


def validate_case_dataset_version(cases, version):
    declared = case_dataset_version(cases)
    if declared is not None and declared != version:
        raise ValueError(f"Case dataset version {declared} does not match {version}")


def load_cases(path=DEFAULT_CASES):
    raw = load_json(path)
    if not isinstance(raw, list) or not raw:
        raise ValueError("Cases must be a nonempty JSON list")
    cases = []
    for item in raw:
        if isinstance(item, list):
            if len(item) != len(CASE_FIELDS):
                raise ValueError("Case arrays must contain exactly seven fields")
            item = dict(zip(CASE_FIELDS, item))
        if not isinstance(item, dict) or any(k not in item for k in CASE_FIELDS):
            raise ValueError("Case is missing required fields")
        if not all(text(item[k]) for k in ("case_id", "encounter_id", "current_transcript")):
            raise ValueError("Case IDs and current transcripts cannot be blank")
        for field in ("update_number", "elapsed_minutes", "eta_minutes"):
            if not number(item[field], integer=True):
                raise ValueError(f"Invalid {field} for {item['case_id']}")
        cases.append(dict(item))
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    case_dataset_version(cases)
    return cases


def blank_reference(case_id):
    return {"case_id": case_id, "reference_summary": None, "meaningful_change": None,
            "explanation_missing_information": None, "preparation_category": None,
            "status": "Not started", "reviewer_id": None, "rubric_version": None,
            "approved": False}


def reference_errors(row):
    errors = []
    if row.get("approved") is not True:
        errors.append("not approved")
    if row.get("status") != "Done":
        errors.append("review not Done")
    for key in ("reference_summary", "explanation_missing_information", "reviewer_id", "rubric_version"):
        if not text(row.get(key)):
            errors.append(f"missing {key}")
    if row.get("meaningful_change") not in CHANGE:
        errors.append("missing/invalid meaningful_change")
    if row.get("preparation_category") not in PREPARATION:
        errors.append("missing/invalid preparation_category")
    if "esi_level" in row:
        level = text(row.get("esi_level"))
        if level not in ESI:
            errors.append("missing/invalid esi_level")
        elif row.get("preparation_category") != PREPARATION_FOR_ESI[level]:
            errors.append("preparation_category does not match esi_level")
        if not text(row.get("esi_reason")):
            errors.append("missing esi_reason")
    return errors


def output_errors(output):
    """Checks an output against whichever contract its fields claim: the category one or the ESI one."""
    if not isinstance(output, dict):
        return ["output must be an object"]
    esi = "esi_level" in output
    errors = []
    if set(output) != set(ESI_OUTPUT_FIELDS if esi else OUTPUT_FIELDS):
        errors.append("output fields do not match the contract")
    for key in ("summary", "change_explanation", "missing_information") + (("esi_reason", "risk_reason") if esi else ()):
        if not isinstance(output.get(key), str) or not output[key].strip():
            errors.append(f"missing/invalid {key}")
    if output.get("meaningful_change") not in CHANGE:
        errors.append("invalid meaningful_change")
    if esi:
        if output.get("esi_level") not in ESI:
            errors.append("invalid esi_level")
        risk = output.get("risk_score")
        if isinstance(risk, bool) or risk not in RISK_SCORES:
            errors.append("invalid risk_score")
    elif output.get("preparation_category") not in PREPARATION:
        errors.append("invalid preparation_category")
    return errors
