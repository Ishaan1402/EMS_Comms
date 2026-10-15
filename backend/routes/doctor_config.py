"""
A hospital's doctors' own queue settings: Auto-order on or off, the order they set by hand when
it is off, and the risk fields they add and fill in on each case. None of this changes the
queue rule (case_queue.py), queue history, the AI assessment or the exports.
"""
import json
import math
from typing import Optional

from fastapi import APIRouter, Depends, Request

from backend.database import get_db, query, utc_now_iso
from backend.middleware.auth import APIError, require_role
from backend.realtime import broker
from backend.routes.cases import get_case_for_user, parse_id, read_json_body, user_hospital_id

hospital_router = APIRouter()
case_router = APIRouter()

RISK_FIELD_KINDS = ("yes_no", "number", "text")
MAX_RISK_FIELDS = 10
MAX_LABEL = 40
MAX_TEXT_VALUE = 200
MAX_ORDERED_CASES = 200


def own_hospital(user: dict) -> int:
    hospital_id = user_hospital_id(user)
    if hospital_id is None:
        raise APIError(403, "Your account is not linked to a hospital")
    return hospital_id


def queue_settings(hospital_id: Optional[int]) -> dict:
    """{auto_order, doctor_order}; Auto-order is on until a doctor turns it off."""
    rows = query("SELECT auto_order, doctor_order FROM hospital_queue_settings WHERE hospital_id = ?",
                 (hospital_id,)) if hospital_id is not None else []
    if not rows:
        return {"auto_order": True, "doctor_order": []}
    return {"auto_order": bool(rows[0]["auto_order"]), "doctor_order": json.loads(rows[0]["doctor_order"])}


def risk_fields(hospital_id: int) -> list:
    return query("""SELECT id, label, kind FROM hospital_risk_fields
                    WHERE hospital_id = ? AND removed_at IS NULL ORDER BY id""", (hospital_id,))


def save_queue_settings(hospital_id: int, column: str, value) -> None:
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO hospital_queue_settings (hospital_id) VALUES (?)", (hospital_id,))
        conn.execute(f"UPDATE hospital_queue_settings SET {column} = ?, updated_at = ? WHERE hospital_id = ?",
                     (value, utc_now_iso(), hospital_id))
    broker.publish("queue.reordered", {}, hospital_id=hospital_id)


@hospital_router.get("/mine/config")
async def get_config(current_user: dict = Depends(require_role(["doctor"]))):
    hospital_id = own_hospital(current_user)
    return {"auto_order": queue_settings(hospital_id)["auto_order"], "risk_fields": risk_fields(hospital_id)}


@hospital_router.put("/mine/auto-order")
async def set_auto_order(request: Request, current_user: dict = Depends(require_role(["doctor"]))):
    hospital_id = own_hospital(current_user)
    body = await read_json_body(request)
    if type(body.get("auto_order")) is not bool:
        raise APIError(400, "auto_order must be true or false")
    save_queue_settings(hospital_id, "auto_order", int(body["auto_order"]))
    return {"auto_order": body["auto_order"]}


@hospital_router.put("/mine/doctor-order")
async def set_doctor_order(request: Request, current_user: dict = Depends(require_role(["doctor"]))):
    """The queue's case ids top to bottom, as the doctor arranged them."""
    hospital_id = own_hospital(current_user)
    case_ids = (await read_json_body(request)).get("case_ids")
    if (not isinstance(case_ids, list) or len(case_ids) > MAX_ORDERED_CASES
            or any(type(case_id) is not int for case_id in case_ids) or len(set(case_ids)) != len(case_ids)):
        raise APIError(400, "case_ids must be a list of distinct case ids")
    save_queue_settings(hospital_id, "doctor_order", json.dumps(case_ids))
    return {"case_ids": case_ids}


@hospital_router.post("/mine/risk-fields", status_code=201)
async def add_risk_field(request: Request, current_user: dict = Depends(require_role(["doctor"]))):
    hospital_id = own_hospital(current_user)
    body = await read_json_body(request)
    label = body.get("label").strip() if isinstance(body.get("label"), str) else ""
    if not 1 <= len(label) <= MAX_LABEL:
        raise APIError(400, f"Give the field a name of up to {MAX_LABEL} characters")
    if body.get("kind") not in RISK_FIELD_KINDS:
        raise APIError(400, "kind must be yes_no, number or text")
    if len(risk_fields(hospital_id)) >= MAX_RISK_FIELDS:
        raise APIError(400, f"A hospital can have up to {MAX_RISK_FIELDS} risk fields")
    with get_db() as conn:
        field_id = conn.execute("""INSERT INTO hospital_risk_fields (hospital_id, label, kind, created_at)
                                   VALUES (?, ?, ?, ?)""", (hospital_id, label, body["kind"], utc_now_iso())).lastrowid
    fields = risk_fields(hospital_id)
    broker.publish("risk_fields.updated", {"risk_fields": fields}, hospital_id=hospital_id)
    return next(field for field in fields if field["id"] == field_id)


@hospital_router.delete("/mine/risk-fields/{field_id}", status_code=204)
async def remove_risk_field(field_id: str, current_user: dict = Depends(require_role(["doctor"]))):
    hospital_id = own_hospital(current_user)
    with get_db() as conn:
        removed = conn.execute("""UPDATE hospital_risk_fields SET removed_at = ?
                                  WHERE id = ? AND hospital_id = ? AND removed_at IS NULL""",
                               (utc_now_iso(), parse_id(field_id, "Field not found"), hospital_id)).rowcount
    if not removed:
        raise APIError(404, "Field not found")
    broker.publish("risk_fields.updated", {"risk_fields": risk_fields(hospital_id)}, hospital_id=hospital_id)


def case_risk_values(case_id: int) -> dict:
    return {str(row["field_id"]): json.loads(row["value"])
            for row in query("SELECT field_id, value FROM case_risk_values WHERE case_id = ?", (case_id,))}


def check_value(kind: str, value):
    """The value to store for a field of this kind, or a 400; None clears it."""
    if value is None or value == "":
        return None
    if kind == "yes_no" and type(value) is bool:
        return value
    if kind == "number" and type(value) in (int, float) and math.isfinite(value):
        return value
    if kind == "text" and isinstance(value, str) and len(value.strip()) <= MAX_TEXT_VALUE:
        return value.strip() or None
    raise APIError(400, {"yes_no": "Choose yes or no", "number": "Enter a number",
                         "text": f"Keep it under {MAX_TEXT_VALUE} characters"}[kind])


@case_router.get("/{case_id}/risk-values")
async def get_risk_values(case_id: str, current_user: dict = Depends(require_role(["doctor"]))):
    case = get_case_for_user(case_id, current_user)
    return {"values": case_risk_values(case["id"])}


@case_router.put("/{case_id}/risk-values")
async def set_risk_value(case_id: str, request: Request, current_user: dict = Depends(require_role(["doctor"]))):
    """{field_id, value}: one field's value on this case."""
    case = get_case_for_user(case_id, current_user)
    body = await read_json_body(request)
    field = query("""SELECT id, kind FROM hospital_risk_fields
                     WHERE id = ? AND hospital_id = ? AND removed_at IS NULL""",
                  (body.get("field_id") if type(body.get("field_id")) is int else -1,
                   case["destination_hospital_id"]))
    if not field:
        raise APIError(400, "Unknown risk field")
    value = check_value(field[0]["kind"], body.get("value"))
    with get_db() as conn:
        conn.execute("""INSERT INTO case_risk_values (case_id, field_id, value, updated_by, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (case_id, field_id) DO UPDATE SET
                          value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                     (case["id"], field[0]["id"], json.dumps(value), current_user["id"], utc_now_iso()))
    values = case_risk_values(case["id"])
    broker.publish("risk_values.updated", {"case_id": case["id"], "values": values},
                   hospital_id=case["destination_hospital_id"])
    return {"values": values}
