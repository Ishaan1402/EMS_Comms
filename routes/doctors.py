from fastapi import APIRouter, Depends, Query, Request, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Any
import json
from database import query, run, get_db
from middleware.auth import get_current_user, require_role, APIError

router = APIRouter()

class AvailabilityUpdate(BaseModel):
    is_available: Optional[Any] = None

class ResponseRequest(BaseModel):
    response: Optional[str] = None

@router.get("/available")
async def get_available_doctors(request: Request):
    """Get all available doctors (public endpoint)."""
    try:
        doctors = query(
            'SELECT id, first_name, last_name, specialty, phone, email FROM users WHERE role = ? AND is_available = ?',
            ('doctor', 1)
        )
        # Sent even when the request has no Origin header.
        return JSONResponse(
            content=doctors,
            headers={"Access-Control-Allow-Origin": "*"}
        )
    
    except Exception as error:
        print(f"Get doctors error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting doctors"}
        )

@router.get("/notifications")
async def get_doctor_notifications(
    sortBy: Optional[str] = Query("newest"),
    current_user: dict = Depends(require_role(["doctor"]))
):
    """Get doctor's notifications with filtering."""
    try:
        # Determine order clause based on sortBy parameter
        order_clause = ""
        if sortBy == "newest":
            order_clause = "ORDER BY n.sent_at DESC"
        elif sortBy == "priority":
            # Unscored recordings first: they need a person to look, and unknown is not low risk.
            order_clause = "ORDER BY (r.risk_score IS NULL) DESC, r.risk_score DESC, r.priority_level ASC, n.sent_at DESC"
        elif sortBy == "oldest":
            order_clause = "ORDER BY n.sent_at ASC"
        else:
            order_clause = "ORDER BY n.sent_at DESC"
        
        notifications = query(
            f"""SELECT n.*, r.patient_info, r.llm_summary, r.urgency_level, r.risk_score, r.priority_level, 
                      r.chief_complaint, r.vital_signs, r.symptoms, r.recommended_actions, r.critical_info,
                      r.created_at as recording_time, u.first_name as emt_first_name, u.last_name as emt_last_name
               FROM notifications n
               JOIN recordings r ON n.recording_id = r.id
               JOIN users u ON r.emt_id = u.id
               WHERE n.doctor_id = ?
               {order_clause}""",
            (current_user["id"],)
        )
        
        return notifications
    
    except Exception as error:
        print(f"Get notifications error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting notifications"}
        )

@router.get("/recording/{id}")
async def get_recording_details(
    id: str,  # str so non-numeric IDs give 404, not 422
    current_user: dict = Depends(require_role(["doctor"]))
):
    """Get specific recording details."""
    try:
        try:
            recording_id = int(id)
        except ValueError:
            return JSONResponse(
                status_code=404,
                content={"error": "Recording not found"}
            )
        
        recordings = query(
            """SELECT r.*, u.first_name as emt_first_name, u.last_name as emt_last_name
               FROM recordings r
               JOIN users u ON r.emt_id = u.id
               WHERE r.id = ?""",
            (recording_id,)
        )
        
        if len(recordings) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": "Recording not found"}
            )
        
        return recordings[0]
    
    except Exception as error:
        print(f"Get recording error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting recording"}
        )

@router.patch("/availability")
async def update_availability(
    request: Request,
    current_user: dict = Depends(require_role(["doctor"]))
):
    """
    Update doctor availability.
    - Empty body -> is_available=0 (falsy), response OMITS is_available key
    - String "false" -> is_available=1 (truthy), response ECHOES "is_available":"false" (string)
    """
    try:
        # Get request body - reject malformed nonempty JSON
        try:
            body_bytes = await request.body()
            if body_bytes:
                # Nonempty body must be valid JSON
                body = json.loads(body_bytes)
            else:
                # Empty body is OK (treated as {})
                body = {}
        except json.JSONDecodeError:
            # Malformed nonempty JSON -> reject before any write
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid JSON"}
            )
        
        is_available_val = body.get("is_available")
        
        # Truthiness: None/0/False/"" -> 0; anything else, including the string "false", -> 1
        if is_available_val is None or is_available_val == "" or is_available_val is False or is_available_val == 0:
            is_available_int = 0
        else:
            is_available_int = 1
        
        run(
            'UPDATE users SET is_available = ? WHERE id = ?',
            (is_available_int, current_user["id"])
        )
        
        # The response echoes the request value, not the stored 0/1.
        response_obj = {"message": "Availability updated successfully"}
        
        if "is_available" in body:
            response_obj["is_available"] = is_available_val
        
        return response_obj
    
    except Exception as error:
        print(f"Update availability error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error updating availability"}
        )

@router.patch("/notifications/{id}/read")
async def mark_notification_read(
    id: int,
    current_user: dict = Depends(require_role(["doctor"]))
):
    """Mark notification as read."""
    try:
        run(
            'UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE id = ? AND doctor_id = ?',
            (id, current_user["id"])
        )
        
        return {"message": "Notification marked as read"}
    
    except Exception as error:
        print(f"Mark read error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error marking notification as read"}
        )

@router.post("/recordings/{id}/respond")
async def respond_to_recording(
    id: str,
    request: Request,
    current_user: dict = Depends(require_role(["doctor"]))
):
    """
    Respond to a recording.
    A missing response field stores NULL.
    """
    try:
        try:
            recording_id = int(id)
        except ValueError:
            # Nothing can match a non-numeric id, so report success without a write.
            return {"message": "Response recorded successfully"}
        
        # Get request body - reject malformed nonempty JSON
        try:
            body_bytes = await request.body()
            if body_bytes:
                # Nonempty body must be valid JSON
                body = json.loads(body_bytes)
            else:
                # Empty body is OK (treated as {})
                body = {}
        except json.JSONDecodeError:
            # Malformed nonempty JSON -> reject before any write
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid JSON"}
            )
        
        response_text = body.get("response")  # Can be None
        
        run(
            'UPDATE notifications SET response = ? WHERE recording_id = ? AND doctor_id = ?',
            (response_text, recording_id, current_user["id"])
        )
        
        return {"message": "Response recorded successfully"}
    
    except Exception as error:
        print(f"Response error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error recording response"}
        )
