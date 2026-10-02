from fastapi import APIRouter, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from pathlib import Path
import os
import time
import json
from openai import OpenAI
from database import query, run
from middleware.auth import get_current_user, require_role, APIError

router = APIRouter()

# Configure OpenAI (allow None for tests)
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None

# Configure uploads
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_AUDIO_TYPES = [
    "audio/mp3", "audio/mpeg", "audio/wav", "audio/x-wav",
    "audio/m4a", "audio/x-m4a", "audio/aac", "audio/x-aac",
    "audio/ogg", "audio/webm", "audio/mp4"
]

@router.post("/upload", status_code=201)
async def upload_recording(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    patient_info: str = Form(...),
    current_user: dict = Depends(require_role(["emt"]))
):
    """Upload new recording."""
    try:
        # Validate audio file type
        if audio.content_type not in ALLOWED_AUDIO_TYPES:
            return JSONResponse(
                status_code=400,
                content={"error": "Only audio files are allowed"}
            )
        
        # Check file size (50MB limit)
        content = await audio.read()
        if len(content) > 50 * 1024 * 1024:
            return JSONResponse(
                status_code=400,
                content={"error": "File size exceeds 50MB limit"}
            )
        
        # Generate unique filename
        timestamp = int(time.time() * 1000)
        random_suffix = int(time.time() * 1000000) % 1000000000
        file_ext = Path(audio.filename).suffix if audio.filename else ".mp3"
        filename = f"recording-{timestamp}-{random_suffix}{file_ext}"
        audio_file_path = UPLOAD_DIR / filename
        
        # Save file
        with open(audio_file_path, "wb") as f:
            f.write(content)
        
        emt_id = current_user["id"]
        
        # Create recording record
        result = run(
            'INSERT INTO recordings (emt_id, patient_info, audio_file_path) VALUES (?, ?, ?)',
            (emt_id, patient_info, str(audio_file_path))
        )
        
        # Process recording asynchronously
        background_tasks.add_task(process_recording, result["id"], str(audio_file_path))
        
        return {
            "message": "Recording uploaded successfully",
            "recording": {
                "id": result["id"],
                "emt_id": emt_id,
                "patient_info": patient_info,
                "audio_file_path": str(audio_file_path)
            }
        }
    
    except Exception as error:
        print(f"Upload error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error during upload"}
        )

@router.get("/my-recordings")
async def get_my_recordings(current_user: dict = Depends(require_role(["emt"]))):
    """Get all recordings for EMT."""
    try:
        recordings = query(
            'SELECT * FROM recordings WHERE emt_id = ? ORDER BY created_at DESC',
            (current_user["id"],)
        )
        return recordings
    
    except Exception as error:
        print(f"Get recordings error: {error}")
        return JSONResponse(
            status_code=500,
            content={"error": "Server error getting recordings"}
        )

@router.get("/{id}")
async def get_recording(id: int, current_user: dict = Depends(get_current_user)):
    """Get recording by ID."""
    try:
        recordings = query(
            'SELECT r.*, u.first_name as emt_first_name, u.last_name as emt_last_name FROM recordings r JOIN users u ON r.emt_id = u.id WHERE r.id = ?',
            (id,)
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

# Background processing functions

async def process_recording(recording_id: int, audio_file_path: str):
    """Process recording (transcription + LLM analysis)."""
    try:
        # Update status to processing
        run(
            'UPDATE recordings SET status = ? WHERE id = ?',
            ('processing', recording_id)
        )
        
        # Step 1: Get patient information from database
        recording = query(
            'SELECT patient_info FROM recordings WHERE id = ?',
            (recording_id,)
        )
        
        patient_info = recording[0]["patient_info"] if recording else ""
        
        # Step 2: Transcribe audio using OpenAI Whisper
        transcription = await transcribe_audio(audio_file_path)
        
        # Step 3: Analyze with LLM (including both transcription and patient info)
        analysis = await analyze_with_llm(transcription, patient_info)
        
        # Step 4: Update recording with structured results
        run(
            """UPDATE recordings SET 
                transcription = ?, 
                llm_summary = ?, 
                risk_score = ?, 
                priority_level = ?, 
                chief_complaint = ?, 
                vital_signs = ?, 
                symptoms = ?, 
                recommended_actions = ?, 
                critical_info = ?,
                status = ? 
            WHERE id = ?""",
            (
                transcription,
                analysis.get("medical_summary") or json.dumps(analysis),
                analysis.get("risk_score", 5),
                analysis.get("priority_level", 3),
                analysis.get("chief_complaint", "Not specified"),
                analysis.get("vital_signs", "Not recorded"),
                analysis.get("symptoms", "Not specified"),
                analysis.get("recommended_actions", "Standard care"),
                analysis.get("critical_info", "None"),
                'completed',
                recording_id
            )
        )
        
        # Step 5: Notify appropriate doctors
        await notify_doctors(recording_id, analysis.get("medical_summary") or json.dumps(analysis))
    
    except Exception as error:
        print(f"Processing error: {error}")
        run(
            'UPDATE recordings SET status = ? WHERE id = ?',
            ('error', recording_id)
        )

async def transcribe_audio(audio_file_path: str) -> str:
    """Transcribe audio using OpenAI Whisper."""
    if not openai_client:
        raise Exception("OpenAI API key not configured")
    
    try:
        with open(audio_file_path, "rb") as audio_file:
            transcription = openai_client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-1",
                response_format="text"
            )
        return transcription
    
    except Exception as error:
        print(f"Transcription error: {error}")
        raise error

async def analyze_with_llm(transcription: str, patient_info: str = "") -> dict:
    """Analyze transcription with LLM."""
    if not openai_client:
        # Return default values if OpenAI not configured
        return {
            "chief_complaint": "Unable to analyze - OpenAI not configured",
            "vital_signs": "Not available",
            "symptoms": "Not specified",
            "risk_score": 5,
            "priority_level": 3,
            "urgency_level": "moderate",
            "recommended_actions": "Manual review required",
            "critical_info": "AI analysis not available",
            "medical_summary": "OpenAI API key not configured"
        }
    
    try:
        prompt = f"""You are an emergency medicine AI specialist. Analyze this EMT conversation and patient information to provide a comprehensive medical assessment.

AUDIO TRANSCRIPTION: {transcription}

ADDITIONAL PATIENT INFORMATION: {patient_info}

Analyze BOTH the audio conversation AND the typed patient information to provide a complete medical assessment.

Return ONLY this JSON structure (no other text):
{{
  "chief_complaint": "Brief description of the patient's main complaint",
  "vital_signs": "Any vital signs mentioned (BP, HR, RR, Temp, O2 Sat, etc.)",
  "symptoms": "Key symptoms observed or reported",
  "risk_score": 5,
  "priority_level": 3,
  "urgency_level": "urgent",
  "recommended_actions": "Specific medical actions recommended",
  "critical_info": "Critical information for doctors",
  "medical_summary": "Comprehensive medical summary"
}}

RISK SCORING GUIDELINES (be precise):
- Risk 9-10: Multiple severe traumas, cardiac arrest, severe bleeding, unconscious/unresponsive
- Risk 7-8: Single severe trauma (major car accident with multiple injuries), severe head injury, chest trauma
- Risk 5-6: Moderate trauma (broken bones, moderate injuries), stable but injured patients
- Risk 3-4: Minor injuries (cuts, bruises, minor fractures), stable patients with minor complaints
- Risk 1-2: Very minor issues (heartburn, minor cuts), routine care patients

PRIORITY LEVEL GUIDELINES:
- Priority 1: Critical (risk 9-10)
- Priority 2: High (risk 7-8) 
- Priority 3: Medium (risk 5-6)
- Priority 4: Low (risk 3-4)
- Priority 5: Routine (risk 1-2)

URGENCY LEVELS:
- "critical": Risk 9-10
- "urgent": Risk 7-8
- "moderate": Risk 5-6
- "low": Risk 3-4
- "routine": Risk 1-2

EXAMPLES:
- "Patient dying, fell from bridge, then run over by car" = Risk 9-10
- "Major car accident, multiple injuries, unconscious" = Risk 7-8
- "Car accident, broken arm, stable vital signs" = Risk 5-6
- "Minor car accident, cuts and bruises" = Risk 3-4
- "Heartburn, feeling fine" = Risk 1-2

Return ONLY the JSON object with no additional text."""

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000
        )
        
        ai_response = response.choices[0].message.content.strip()
        print(f"Raw AI response: {ai_response}")
        
        # Clean the response to extract JSON
        cleaned_response = ai_response
        if "```json" in ai_response:
            cleaned_response = ai_response.split("```json")[1].split("```")[0].strip()
        elif "```" in ai_response:
            cleaned_response = ai_response.split("```")[1].split("```")[0].strip()
        
        result = json.loads(cleaned_response)
        print(f"Cleaned AI response: {result}")
        return result
    
    except Exception as error:
        print(f"LLM analysis error: {error}")
        # Return default values if AI analysis fails
        return {
            "chief_complaint": "Unable to analyze",
            "vital_signs": "Not available",
            "symptoms": "Not specified",
            "risk_score": 5,
            "priority_level": 3,
            "urgency_level": "moderate",
            "recommended_actions": "Manual review required",
            "critical_info": "AI analysis failed",
            "medical_summary": "Unable to generate medical summary"
        }

async def notify_doctors(recording_id: int, summary: str):
    """Notify appropriate doctors."""
    try:
        # Get available doctors
        doctors = query(
            'SELECT * FROM users WHERE role = ? AND is_available = ?',
            ('doctor', 1)
        )
        
        # For now, notify all available doctors
        # In production, you'd implement specialty matching
        for doctor in doctors:
            run(
                'INSERT INTO notifications (recording_id, doctor_id, notification_type) VALUES (?, ?, ?)',
                (recording_id, doctor["id"], 'both')
            )
        
        # Update recording status
        run(
            'UPDATE recordings SET status = ? WHERE id = ?',
            ('notified', recording_id)
        )
    
    except Exception as error:
        print(f"Notification error: {error}")
        raise error
