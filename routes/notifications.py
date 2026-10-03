from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
import asyncio
from twilio.rest import Client as TwilioClient
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from datetime import datetime
from database import query, run, get_db
from middleware.auth import get_current_user, require_role, APIError
from routes.recordings import slow_executor

router = APIRouter()

# Clients are None when unconfigured; the send helpers raise if used.
twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_client = TwilioClient(twilio_account_sid, twilio_auth_token) if (twilio_account_sid and twilio_auth_token) else None

sendgrid_api_key = os.getenv("SENDGRID_API_KEY")
sendgrid_client = SendGridAPIClient(sendgrid_api_key) if sendgrid_api_key else None

class TestNotificationRequest(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None

@router.post("/send")
async def send_notification(
    request: Request,
    current_user: dict = Depends(require_role(["emt"]))
):
    """
    Send notification to doctor.
    Missing fields or an array body -> 404 (lookup fails), never 422.
    Twilio/SendGrid failures -> 500 and delivered stays 0.
    """
    try:
        try:
            body = await request.json()
            if isinstance(body, list):
                body = {}
        except:
            body = {}
        
        recording_id = body.get("recording_id")
        doctor_id = body.get("doctor_id")
        notification_type = body.get("notification_type")
        
        recordings = query(
            'SELECT * FROM recordings WHERE id = ?',
            (recording_id,) if recording_id else (None,)
        )
        
        doctors = query(
            'SELECT * FROM users WHERE id = ?',
            (doctor_id,) if doctor_id else (None,)
        )
        
        if len(recordings) == 0 or len(doctors) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": "Recording or doctor not found"}
            )
        
        recording_data = recordings[0]
        doctor_data = doctors[0]
        
        if notification_type == "sms" or notification_type == "both":
            await send_sms(doctor_data["phone"], recording_data, doctor_data)
        
        if notification_type == "email" or notification_type == "both":
            await send_email(doctor_data["email"], recording_data, doctor_data)
        
        run(
            'UPDATE notifications SET delivered = 1 WHERE recording_id = ? AND doctor_id = ?',
            (recording_id, doctor_id)
        )
        
        return {"message": "Notification sent successfully"}
    
    except Exception as error:
        print(f"Send notification error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error sending notification"}
        )

async def send_sms(phone_number: str, recording: dict, doctor: dict):
    """Send SMS notification. Missing values render literally as "undefined"/"null"."""
    if not twilio_client:
        raise Exception("Twilio not configured")
    
    try:
        # A missing summary renders as "undefined..."
        summary = recording.get("llm_summary")
        if summary is None:
            summary_preview = "undefined"
        elif summary == "":
            summary_preview = ""
        else:
            summary_preview = summary[:100]
        summary_preview += "..."
        
        # Missing EMT names render as "EMT: undefined undefined"
        emt_first_name = recording.get("emt_first_name")
        emt_last_name = recording.get("emt_last_name")
        if emt_first_name is None:
            emt_first_name = "undefined"
        if emt_last_name is None:
            emt_last_name = "undefined"
        
        urgency_level = recording.get("urgency_level")
        if urgency_level is None:
            urgency_level = "null"
        elif urgency_level == "":
            urgency_level = ""
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        
        message = f"""🚑 EMERGENCY ALERT - Dr. {doctor['last_name']}

Patient Summary: {summary_preview}

Urgency: {urgency_level}
EMT: {emt_first_name} {emt_last_name}

View full details at: {frontend_url}/recording/{recording['id']}

Reply STOP to unsubscribe"""

        await asyncio.get_running_loop().run_in_executor(
            slow_executor,
            lambda: twilio_client.messages.create(
                body=message,
                from_=os.getenv("TWILIO_PHONE_NUMBER"),
                to=phone_number
            )
        )
        
        print(f"SMS sent to {phone_number}")
    
    except Exception as error:
        print(f"SMS error: {error}")
        raise error

async def send_email(email: str, recording: dict, doctor: dict):
    """Send email notification. Missing values render literally as "undefined"/"null"."""
    if not sendgrid_client:
        raise Exception("SendGrid not configured")
    
    try:
        summary = recording.get("llm_summary")
        if summary is None:
            summary = "null"
        elif summary == "":
            summary = ""
        
        emt_first_name = recording.get("emt_first_name")
        emt_last_name = recording.get("emt_last_name")
        if emt_first_name is None:
            emt_first_name = "undefined"
        if emt_last_name is None:
            emt_last_name = "undefined"
        
        urgency_level = recording.get("urgency_level")
        if urgency_level is None:
            urgency_level = "undefined"  # the email says "undefined" where the SMS says "null"
        elif urgency_level == "":
            urgency_level = ""
        
        created_at = recording.get("created_at", "")
        
        # Format as "10/2/2026, 9:00:00 PM" (no zero-padding on month, day, or hour)
        try:
            from datetime import datetime as dt
            dt_obj = dt.fromisoformat(created_at.replace("Z", "+00:00"))
            # lstrip instead of %-d, which is not portable to Windows
            month = dt_obj.strftime("%m").lstrip("0") or "0"
            day = dt_obj.strftime("%d").lstrip("0") or "0"
            year = dt_obj.strftime("%Y")
            time_part = dt_obj.strftime("%I:%M:%S %p").lstrip("0")
            formatted_time = f"{month}/{day}/{year}, {time_part}"
        except:
            formatted_time = str(created_at)
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        urgency_color = get_urgency_color(urgency_level)
        
        # Uppercase real urgency values; a missing one stays lowercase "undefined"
        if urgency_level and urgency_level != "null" and urgency_level != "undefined":
            urgency_display = urgency_level.upper()
        else:
            urgency_display = urgency_level
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
          <h2 style="color: #d32f2f;">🚑 EMERGENCY ALERT</h2>
          
          <div style="background-color: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3>Patient Summary</h3>
            <p style="white-space: pre-wrap;">{summary}</p>
          </div>
          
          <div style="margin: 20px 0;">
            <p><strong>Urgency Level:</strong> <span style="color: {urgency_color};">{urgency_display}</span></p>
            <p><strong>EMT:</strong> {emt_first_name} {emt_last_name}</p>
            <p><strong>Time:</strong> {formatted_time}</p>
          </div>
          
          <div style="text-align: center; margin: 30px 0;">
            <a href="{frontend_url}/recording/{recording['id']}" 
               style="background-color: #1976d2; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; display: inline-block;">
              View Full Details
            </a>
          </div>
          
          <hr style="margin: 30px 0;">
          <p style="color: #666; font-size: 12px;">
            This is an automated notification from the Shealthcare EMT System.
            Please do not reply to this email.
          </p>
        </div>
        """
        
        message = Mail(
            from_email=os.getenv("SENDGRID_FROM_EMAIL"),
            to_emails=email,
            subject=f"🚑 Emergency Alert - Patient Summary for Dr. {doctor['last_name']}",
            html_content=html_content
        )
        
        await asyncio.get_running_loop().run_in_executor(slow_executor, sendgrid_client.send, message)
        print(f"Email sent to {email}")
    
    except Exception as error:
        print(f"Email error: {error}")
        raise error

def get_urgency_color(urgency: str) -> str:
    """Get urgency color for email styling."""
    urgency_colors = {
        "critical": "#d32f2f",
        "high": "#f57c00",
        "medium": "#fbc02d",
        "low": "#388e3c"
    }
    return urgency_colors.get(urgency, "#666")

@router.get("/status/{id}")
async def get_notification_status(
    id: str,  # str so non-numeric IDs give 404, not 422
    current_user: dict = Depends(get_current_user)
):
    """Get notification status."""
    try:
        try:
            notification_id = int(id)
        except ValueError:
            return JSONResponse(
                status_code=404,
                content={"error": "Notification not found"}
            )
        
        notifications = query(
            'SELECT * FROM notifications WHERE id = ?',
            (notification_id,)
        )
        
        if len(notifications) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": "Notification not found"}
            )
        
        return notifications[0]
    
    except Exception as error:
        print(f"Get notification status error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting notification status"}
        )

@router.post("/test")
async def test_notification(
    req: TestNotificationRequest,
    current_user: dict = Depends(require_role(["emt"]))
):
    """Test notification endpoint (for development)."""
    try:
        test_recording = {
            "llm_summary": "This is a test notification from the Shealthcare EMT System.",
            "urgency_level": "medium",
            "emt_first_name": "Test",
            "emt_last_name": "EMT",
            "id": "test-123",
            "created_at": datetime.now().isoformat()
        }
        
        test_doctor = {
            "last_name": "Test"
        }
        
        if req.phone:
            test_doctor["phone"] = req.phone
            await send_sms(req.phone, test_recording, test_doctor)
        
        if req.email:
            test_doctor["email"] = req.email
            await send_email(req.email, test_recording, test_doctor)
        
        return {"message": "Test notification sent successfully"}
    
    except Exception as error:
        print(f"Test notification error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error sending test notification"}
        )
