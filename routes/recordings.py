from fastapi import APIRouter, Depends, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pathlib import Path
import os
import time
import json
import asyncio
import concurrent.futures
from openai import OpenAI
from database import query, run, get_db
from middleware.auth import get_current_user, require_role, APIError

router = APIRouter()

# Configure OpenAI (allow None for tests)
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None

# Configure uploads
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Match Express multer regex: /audio\/(mp3|wav|m4a|aac|ogg|webm|mp4)/
# Note: Express does NOT include "mpeg" - only mp3, wav, m4a, aac, ogg, webm, mp4
ALLOWED_AUDIO_TYPES = [
    "audio/mp3", "audio/wav", "audio/m4a", "audio/x-m4a",
    "audio/aac", "audio/x-aac", "audio/ogg", "audio/webm", "audio/mp4"
    # Note: "audio/mpeg" is NOT in this list to match Express
]

# Thread pool for OpenAI sync calls (non-blocking)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

@router.post("/upload", status_code=201)
async def upload_recording(
    background_tasks: BackgroundTasks,
    request: Request,
    audio: UploadFile = File(...),
    patient_info: str = Form(None),  # Optional like Express (req.body.patient_info can be undefined)
    current_user: dict = Depends(require_role(["emt"]))
):
    """
    Upload new recording.
    Matches Express multer behavior:
    - Missing audio file -> 500 "Server error during upload" (caught in route)
    - Wrong MIME type -> 500 "Something went wrong!" (global handler)
    - File too large -> 500 "Something went wrong!" (global handler)
    - patient_info optional (omit key if None)
    """
    try:
        # Validate audio file type - if wrong, raise to global handler
        if audio.content_type not in ALLOWED_AUDIO_TYPES:
            # Express multer fileFilter error goes to global handler -> "Something went wrong!"
            raise Exception(f"Multer file filter error: Invalid file type")
        
        # Check file size (50MB limit) - if exceeded, raise to global handler
        content = await audio.read()
        if len(content) > 50 * 1024 * 1024:
            # Express multer size limit error goes to global handler -> "Something went wrong!"
            raise Exception(f"Multer size limit exceeded")
        
        # Generate unique filename like Express
        timestamp = int(time.time() * 1000)
        random_suffix = int(time.time() * 1000000) % 1000000000
        file_ext = Path(audio.filename).suffix if audio.filename else ".mp3"
        filename = f"recording-{timestamp}-{random_suffix}{file_ext}"
        audio_file_path = UPLOAD_DIR / filename
        
        # Save file (blocking I/O in threadpool)
        await asyncio.get_running_loop().run_in_executor(executor, audio_file_path.write_bytes, content)
        
        emt_id = current_user["id"]
        
        # Create recording record - patient_info can be None (NULL in SQL)
        result = run(
            'INSERT INTO recordings (emt_id, patient_info, audio_file_path) VALUES (?, ?, ?)',
            (emt_id, patient_info, str(audio_file_path))
        )
        
        # Process recording asynchronously (fire and forget like Express)
        background_tasks.add_task(process_recording, result["id"], str(audio_file_path))
        
        # Build response - OMIT patient_info key when null/None like Express
        # Express key order: id, emt_id, patient_info, audio_file_path
        recording_obj = {
            "id": result["id"],
            "emt_id": emt_id
        }
        # Only include patient_info if not None (insert before audio_file_path)
        if patient_info is not None:
            recording_obj["patient_info"] = patient_info
        recording_obj["audio_file_path"] = str(audio_file_path)
        
        return {
            "message": "Recording uploaded successfully",
            "recording": recording_obj
        }
    
    except Exception as error:
        # Check if it's a multer-style error (should go to global handler)
        error_str = str(error)
        if "Multer" in error_str or "file filter" in error_str or "size limit" in error_str:
            # Re-raise to be caught by global handler -> "Something went wrong!"
            raise
        
        # Otherwise it's a route-level error -> "Server error during upload"
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
async def get_recording(id: str, request: Request):  # Accept string, manual auth to avoid dep issues
    """Get recording by ID - Express-compatible."""
    # Manual auth to match Express (doesn't use strict role check)
    current_user = get_current_user(request)
    
    try:
        # Try to convert to int
        try:
            recording_id = int(id)
        except ValueError:
            return JSONResponse(
                status_code=404,
                content={"error": "Recording not found"}
            )
        
        recordings = query(
            'SELECT r.*, u.first_name as emt_first_name, u.last_name as emt_last_name FROM recordings r JOIN users u ON r.emt_id = u.id WHERE r.id = ?',
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

# Background processing functions

async def process_recording(recording_id: int, audio_file_path: str):
    """
    Process recording (transcription + LLM analysis).
    Matches Express: blocking I/O in threadpool, apply || defaults at storage time.
    """
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
        
        # Step 2: Transcribe audio (blocking I/O in threadpool)
        loop = asyncio.get_event_loop()
        transcription = await loop.run_in_executor(executor, transcribe_audio_sync, audio_file_path)
        
        # Step 3: Analyze with LLM (returns RAW response)
        analysis = await analyze_with_llm(transcription, patient_info)
        
        # Step 4: Apply JavaScript || semantics at storage time (like Express)
        # Store llm_summary as compact JSON (json.dumps with no spaces, like JSON.stringify)
        llm_summary_value = analysis.get("medical_summary")
        if not llm_summary_value:
            # Store compact JSON without spaces (JSON.stringify default)
            llm_summary_value = json.dumps(analysis, separators=(',', ':'))
        
        # Apply || defaults for column fields
        risk_score_value = analysis.get("risk_score") or 5
        priority_level_value = analysis.get("priority_level") or 3
        chief_complaint_value = analysis.get("chief_complaint") or "Not specified"
        vital_signs_value = analysis.get("vital_signs") or "Not recorded"
        symptoms_value = analysis.get("symptoms") or "Not specified"
        recommended_actions_value = analysis.get("recommended_actions") or "Standard care"
        critical_info_value = analysis.get("critical_info") or "None"
        # DO NOT set urgency_level - Express does not set this column in processRecording
        # It stays at DB default "medium"
        
        # Update recording with structured results (no urgency_level update)
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
                llm_summary_value,
                risk_score_value,
                priority_level_value,
                chief_complaint_value,
                vital_signs_value,
                symptoms_value,
                recommended_actions_value,
                critical_info_value,
                'completed',
                recording_id
            )
        )
        
        print(f"✅ Recording {recording_id} processed successfully")
        
        # Step 5: Notify appropriate doctors
        await notify_doctors(recording_id, llm_summary_value)
    
    except Exception as error:
        print(f"❌ Processing error for recording {recording_id}: {error}")
        run(
            'UPDATE recordings SET status = ? WHERE id = ?',
            ('error', recording_id)
        )

def transcribe_audio_sync(audio_file_path: str) -> str:
    """Sync version of transcribe for thread pool execution."""
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

async def transcribe_audio(audio_file_path: str) -> str:
    """Transcribe audio using OpenAI Whisper."""
    return transcribe_audio_sync(audio_file_path)

async def analyze_with_llm(transcription: str, patient_info: str = "") -> dict:
    """
    Analyze transcription with LLM - returns RAW LLM response.
    Raises on a missing key or model failure so the recording is marked error
    instead of storing a fake assessment and notifying doctors.
    """
    if not openai_client:
        raise Exception("OpenAI API key not configured")
    
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

        # Run OpenAI sync call in thread pool (non-blocking)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            executor,
            lambda: openai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000
            )
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
        
        # Return RAW result - do NOT apply defaults here
        # Express applies defaults only at storage time for specific columns
        return result

    except Exception as error:
        print(f"LLM analysis error: {error}")
        raise

async def notify_doctors(recording_id: int, summary: str):
    """Notify appropriate doctors. All inserts and the status change commit together or not at all."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            # For now, notify all available doctors
            # In production, you'd implement specialty matching
            cursor.execute(
                'SELECT id FROM users WHERE role = ? AND is_available = ?',
                ('doctor', 1)
            )
            doctor_ids = [row["id"] for row in cursor.fetchall()]

            cursor.executemany(
                'INSERT INTO notifications (recording_id, doctor_id, notification_type) VALUES (?, ?, ?)',
                [(recording_id, doctor_id, 'both') for doctor_id in doctor_ids]
            )

            cursor.execute(
                'UPDATE recordings SET status = ? WHERE id = ?',
                ('notified', recording_id)
            )
    
    except Exception as error:
        print(f"Notification error: {error}")
        raise error
