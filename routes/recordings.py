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

# None when OPENAI_API_KEY is unset; processing then marks recordings as error.
openai_api_key = os.getenv("OPENAI_API_KEY")
# One SDK retry at most: with the default two, a call could outlast the caller's own timeout
# and keep a worker thread busy after the caller gave up. Transcription retries on its own.
OPENAI_MAX_RETRIES = 1
openai_client = OpenAI(api_key=openai_api_key, max_retries=OPENAI_MAX_RETRIES) if openai_api_key else None
# gpt-4 shuts down 2026-10-23. gpt-6-luna is the cheapest current model and the one evals/ uses.
SCORING_MODEL = os.getenv("OPENAI_SCORING_MODEL", "gpt-6-luna")
# whisper-1 is deprecated and misheard short live segments ("awake and" -> "Awaken");
# gpt-transcribe got them right in a side-by-side test and costs less per minute.
TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-transcribe")
# Sent only when set: models without reasoning reject the parameter. Blank it for those.
SCORING_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "none")
# A hung call would otherwise hold a worker for the client's 10-minute default.
SCORING_REQUEST_TIMEOUT_SECONDS = 40


def scoring_extra_body() -> dict:
    """Newer models take max_completion_tokens and a reasoning effort, not temperature/max_tokens."""
    body = {"max_completion_tokens": 1000}
    if SCORING_REASONING_EFFORT:
        body["reasoning_effort"] = SCORING_REASONING_EFFORT
    return body


def bounded_int(value, low: int, high: int):
    """An integer in [low, high] from model output, else None: never a made-up default."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and low <= value <= high else None

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# audio/mpeg is deliberately not accepted.
ALLOWED_AUDIO_TYPES = [
    "audio/mp3", "audio/wav", "audio/m4a", "audio/x-m4a",
    "audio/aac", "audio/x-aac", "audio/ogg", "audio/webm", "audio/mp4"
]

def is_audio_type_allowed(content_type: str) -> bool:
    """Check if audio MIME type is allowed, handling parameters like codecs."""
    if not content_type:
        return False
    # Parse base MIME type (before semicolon) to handle parameterized types
    base_type = content_type.split(';')[0].strip()
    return base_type in ALLOWED_AUDIO_TYPES

# Separate pools: fast ops (auth, file writes) vs slow external calls (OpenAI, notifications)
fast_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="fast")
slow_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="slow")

# The recording's EMT and the doctors notified about it; anyone else gets the same 404 as a missing one.
VISIBLE_RECORDING = """ AND (r.emt_id = ? OR EXISTS (
    SELECT 1 FROM notifications n WHERE n.recording_id = r.id AND n.doctor_id = ?))"""


@router.post("/upload", status_code=201)
async def upload_recording(
    background_tasks: BackgroundTasks,
    request: Request,
    audio: UploadFile = File(...),
    patient_info: str = Form(None),
    current_user: dict = Depends(require_role(["emt"]))
):
    """
    Upload new recording.
    - Missing audio file -> 500 "Server error during upload" (caught in route)
    - Wrong MIME type -> 500 "Something went wrong!" (global handler)
    - File too large -> 500 "Something went wrong!" (global handler)
    - patient_info optional (omit key if None)
    """
    try:
        # Type and size errors go to the global handler ("Something went wrong!").
        if not is_audio_type_allowed(audio.content_type):
            raise Exception(f"Multer file filter error: Invalid file type")
        
        content = await audio.read()
        if len(content) > 50 * 1024 * 1024:
            raise Exception(f"Multer size limit exceeded")
        
        timestamp = int(time.time() * 1000)
        random_suffix = int(time.time() * 1000000) % 1000000000
        file_ext = Path(audio.filename).suffix if audio.filename else ".mp3"
        filename = f"recording-{timestamp}-{random_suffix}{file_ext}"
        audio_file_path = UPLOAD_DIR / filename
        
        await asyncio.get_running_loop().run_in_executor(fast_executor, audio_file_path.write_bytes, content)
        
        emt_id = current_user["id"]
        
        # No score until one is produced: the column defaults (risk 5, priority 3) look like a real assessment.
        result = run(
            'INSERT INTO recordings (emt_id, patient_info, audio_file_path, risk_score, priority_level) VALUES (?, ?, ?, NULL, NULL)',
            (emt_id, patient_info, str(audio_file_path))
        )
        
        background_tasks.add_task(process_recording, result["id"], str(audio_file_path))
        
        # Key order is id, emt_id, patient_info, audio_file_path; patient_info is omitted when None.
        recording_obj = {
            "id": result["id"],
            "emt_id": emt_id
        }
        if patient_info is not None:
            recording_obj["patient_info"] = patient_info
        recording_obj["audio_file_path"] = str(audio_file_path)
        
        return {
            "message": "Recording uploaded successfully",
            "recording": recording_obj
        }
    
    except Exception as error:
        error_str = str(error)
        if "Multer" in error_str or "file filter" in error_str or "size limit" in error_str:
            raise
        
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
async def get_recording(id: str, request: Request):
    """Get recording by ID. Any authenticated role may read it."""
    current_user = get_current_user(request)
    
    try:
        try:
            recording_id = int(id)
        except ValueError:
            return JSONResponse(
                status_code=404,
                content={"error": "Recording not found"}
            )
        
        recordings = query(
            'SELECT r.*, u.first_name as emt_first_name, u.last_name as emt_last_name FROM recordings r JOIN users u ON r.emt_id = u.id WHERE r.id = ?'
            + VISIBLE_RECORDING,
            (recording_id, current_user.get("id"), current_user.get("id"))
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

async def process_recording(recording_id: int, audio_file_path: str):
    """
    Process recording (transcription + LLM analysis).
    Defaults for empty text fields are applied at storage time; scores are never defaulted.
    """
    try:
        run(
            'UPDATE recordings SET status = ? WHERE id = ?',
            ('processing', recording_id)
        )
        
        recording = query(
            'SELECT patient_info FROM recordings WHERE id = ?',
            (recording_id,)
        )
        
        patient_info = recording[0]["patient_info"] if recording else ""
        
        loop = asyncio.get_event_loop()
        transcription = await loop.run_in_executor(slow_executor, transcribe_audio_sync, audio_file_path)
        
        analysis = await analyze_with_llm(transcription, patient_info)
        
        # Without a medical_summary, store the whole analysis as compact JSON.
        llm_summary_value = analysis.get("medical_summary")
        if not llm_summary_value:
            llm_summary_value = json.dumps(analysis, separators=(',', ':'))
        
        # A missing or invalid score stays NULL (shown as "unavailable"), never a normal-looking default.
        risk_score_value = bounded_int(analysis.get("risk_score"), 0, 10)
        priority_level_value = bounded_int(analysis.get("priority_level"), 1, 5)
        chief_complaint_value = analysis.get("chief_complaint") or "Not specified"
        vital_signs_value = analysis.get("vital_signs") or "Not recorded"
        symptoms_value = analysis.get("symptoms") or "Not specified"
        recommended_actions_value = analysis.get("recommended_actions") or "Standard care"
        critical_info_value = analysis.get("critical_info") or "None"
        # urgency_level is intentionally not written; it keeps the DB default "medium".
        
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
                model=TRANSCRIPTION_MODEL,
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

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            slow_executor,
            lambda: openai_client.chat.completions.create(
                model=SCORING_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                extra_body=scoring_extra_body(),
                timeout=SCORING_REQUEST_TIMEOUT_SECONDS,
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
        
        # Defaults are applied by the caller at storage time.
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
