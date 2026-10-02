from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
from twilio.rest import Client as TwilioClient
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from datetime import datetime
from database import query, run
from middleware.auth import get_current_user, require_role, APIError

router = APIRouter()

# Configure Twilio (allow None for tests)
twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_client = TwilioClient(twilio_account_sid, twilio_auth_token) if (twilio_account_sid and twilio_auth_token) else None

# Configure SendGrid (allow None for tests)
sendgrid_api_key = os.getenv("SENDGRID_API_KEY")
sendgrid_client = SendGridAPIClient(sendgrid_api_key) if sendgrid_api_key else None

class SendNotificationRequest(BaseModel):
    recording_id: int
    doctor_id: int
    notification_type: str

class TestNotificationRequest(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None

@router.post("/send")
async def send_notification(
    req: SendNotificationRequest,
    current_user: dict = Depends(require_role(["emt"]))
):
    """Send notification to doctor."""
    try:
        # Get recording and doctor details
        recordings = query(
            'SELECT * FROM recordings WHERE id = ?',
            (req.recording_id,)
        )
        
        doctors = query(
            'SELECT * FROM users WHERE id = ?',
            (req.doctor_id,)
        )
        
        if len(recordings) == 0 or len(doctors) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": "Recording or doctor not found"}
            )
        
        recording_data = recordings[0]
        doctor_data = doctors[0]
        
        # Send notifications based on type
        if req.notification_type == "sms" or req.notification_type == "both":
            await send_sms(doctor_data["phone"], recording_data, doctor_data)
        
        if req.notification_type == "email" or req.notification_type == "both":
            await send_email(doctor_data["email"], recording_data, doctor_data)
        
        # Update notification record
        run(
            'UPDATE notifications SET delivered = 1 WHERE recording_id = ? AND doctor_id = ?',
            (req.recording_id, req.doctor_id)
        )
        
        return {"message": "Notification sent successfully"}
    
    except Exception as error:
        print(f"Send notification error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error sending notification"}
        )

async def send_sms(phone_number: str, recording: dict, doctor: dict):
    """Send SMS notification."""
    if not twilio_client:
        print("Twilio not configured, skipping SMS")
        return
    
    try:
        # Get summary (may be truncated)
        summary = recording.get("llm_summary", "Summary not available")
        summary_preview = summary[:100] if summary else "Summary not available"
        if len(summary) > 100:
            summary_preview += "..."
        
        # Get EMT names (may not be in recording dict)
        emt_first_name = recording.get("emt_first_name", "")
        emt_last_name = recording.get("emt_last_name", "")
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        
        message = f"""🚑 EMERGENCY ALERT - Dr. {doctor['last_name']}

Patient Summary: {summary_preview}

Urgency: {recording.get('urgency_level', 'unknown')}
EMT: {emt_first_name} {emt_last_name}

View full details at: {frontend_url}/recording/{recording['id']}

Reply STOP to unsubscribe"""

        twilio_client.messages.create(
            body=message,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            to=phone_number
        )
        
        print(f"SMS sent to {phone_number}")
    
    except Exception as error:
        print(f"SMS error: {error}")
        raise error

async def send_email(email: str, recording: dict, doctor: dict):
    """Send email notification."""
    if not sendgrid_client:
        print("SendGrid not configured, skipping email")
        return
    
    try:
        # Get data
        summary = recording.get("llm_summary", "Summary not available")
        emt_first_name = recording.get("emt_first_name", "")
        emt_last_name = recording.get("emt_last_name", "")
        urgency_level = recording.get("urgency_level", "unknown")
        created_at = recording.get("created_at", "")
        
        # Format created_at
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            formatted_time = str(created_at)
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        urgency_color = get_urgency_color(urgency_level)
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
          <h2 style="color: #d32f2f;">🚑 EMERGENCY ALERT</h2>
          
          <div style="background-color: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3>Patient Summary</h3>
            <p style="white-space: pre-wrap;">{summary}</p>
          </div>
          
          <div style="margin: 20px 0;">
            <p><strong>Urgency Level:</strong> <span style="color: {urgency_color};">{urgency_level.upper()}</span></p>
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
        
        sendgrid_client.send(message)
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
    id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get notification status."""
    try:
        notifications = query(
            'SELECT * FROM notifications WHERE id = ?',
            (id,)
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
