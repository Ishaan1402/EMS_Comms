# API Parity Checklist: Node/Express → Python/FastAPI

This document ensures complete behavior preservation during the backend port.

## Authentication & Authorization

### JWT Token Behavior
- [x] Payload: `{id, username, role}` (integers/strings as in JS)
- [x] Expiry: 24 hours
- [x] Auth header: `Authorization: Bearer <token>`
- [x] Missing token → 401 with `{"error": "Access denied. No token provided."}`
- [x] Invalid token (verify fails) → 400 with `{"error": "Invalid token."}`
- [x] Insufficient permissions → 403 with `{"error": "Access denied. Insufficient permissions."}`

### Bcrypt Compatibility
- [x] Hash with bcrypt, rounds=10
- [x] Verify existing bcryptjs hashes (from Node backend)
- [x] Demo password: `password123`

## Endpoints

### POST /api/auth/register
- **Auth**: None
- **Role**: None
- **Request Body**: `{username, email, password, role, first_name, last_name, phone?, specialty?}`
- **Success Response (201)**:
  ```json
  {
    "message": "User registered successfully",
    "user": {
      "id": 1,
      "username": "...",
      "email": "...",
      "role": "...",
      "first_name": "...",
      "last_name": "..."
    },
    "token": "..."
  }
  ```
- **Error Responses**:
  - 400: `{"error": "Username or email already exists"}`
  - 500: `{"error": "Server error during registration"}`
- **DB Side Effects**: INSERT user with hashed password
- **Checklist**:
  - [x] Returns 201 on success
  - [x] Returns user object without password_hash
  - [x] Returns JWT token
  - [x] Checks for existing username or email
  - [x] Hashes password with bcrypt rounds=10
  - [x] Validates role field

### POST /api/auth/login
- **Auth**: None
- **Role**: None
- **Request Body**: `{username, password}`
- **Success Response (200)**:
  ```json
  {
    "message": "Login successful",
    "user": {
      "id": 1,
      "username": "...",
      "role": "...",
      "first_name": "...",
      "last_name": "...",
      "specialty": "..."
    },
    "token": "..."
  }
  ```
- **Error Responses**:
  - 400: `{"error": "Invalid credentials"}` (wrong username OR password)
  - 500: `{"error": "Server error during login"}`
- **Checklist**:
  - [x] Returns 200 on success
  - [x] Returns user object with specialty
  - [x] Returns JWT token
  - [x] Returns 400 for both missing user and wrong password (same message)
  - [x] Verifies bcrypt hash

### GET /api/auth/profile
- **Auth**: Required
- **Role**: Any authenticated user
- **Request**: None
- **Success Response (200)**:
  ```json
  {
    "id": 1,
    "username": "...",
    "email": "...",
    "role": "...",
    "first_name": "...",
    "last_name": "...",
    "phone": "...",
    "specialty": "...",
    "is_available": 1
  }
  ```
- **Error Responses**:
  - 401: `{"error": "Access denied. No token provided."}`
  - 400: `{"error": "Invalid token."}`
  - 404: `{"error": "User not found"}`
  - 500: `{"error": "Server error getting profile"}`
- **Checklist**:
  - [x] Returns is_available as 0/1 (integer), not boolean
  - [x] Returns all user fields except password_hash
  - [x] Uses req.user.id from JWT

### POST /api/recordings/upload
- **Auth**: Required
- **Role**: `emt` only
- **Request**: multipart/form-data
  - `audio`: audio file (mp3, wav, m4a, aac, ogg, webm, mp4)
  - `patient_info`: text
- **Success Response (201)**:
  ```json
  {
    "message": "Recording uploaded successfully",
    "recording": {
      "id": 1,
      "emt_id": 1,
      "patient_info": "...",
      "audio_file_path": "uploads/recording-..."
    }
  }
  ```
- **Error Responses**:
  - 401: No token
  - 403: Not EMT role
  - 500: `{"error": "Server error during upload"}`
- **DB Side Effects**: INSERT recording with status='pending'
- **Background Processing**: Triggers async `processRecording()`
- **Checklist**:
  - [x] Accepts multipart with field name `audio`
  - [x] 50MB file size limit
  - [x] Saves to `uploads/` with naming: `recording-{timestamp}-{random}.{ext}`
  - [x] Returns immediately (201) before processing completes
  - [x] Starts background processing (not blocking response)
  - [x] Validates audio MIME types
  - [x] Creates uploads/ directory if missing

### GET /api/recordings/my-recordings
- **Auth**: Required
- **Role**: `emt` only
- **Request**: None
- **Success Response (200)**:
  ```json
  [
    {
      "id": 1,
      "emt_id": 1,
      "patient_info": "...",
      "audio_file_path": "...",
      "transcription": "...",
      "llm_summary": "...",
      "urgency_level": "medium",
      "risk_score": 5,
      "priority_level": 3,
      "chief_complaint": "...",
      "vital_signs": "...",
      "symptoms": "...",
      "recommended_actions": "...",
      "critical_info": "...",
      "status": "completed",
      "created_at": "...",
      "updated_at": "..."
    }
  ]
  ```
- **Error Responses**:
  - 401/403: Auth errors
  - 500: `{"error": "Server error getting recordings"}`
- **Checklist**:
  - [x] Returns recordings for req.user.id only
  - [x] Ordered by created_at DESC
  - [x] Returns all recording fields
  - [x] is_available and other booleans as 0/1

### GET /api/recordings/:id
- **Auth**: Required
- **Role**: Any authenticated user
- **Request**: Path param `id`
- **Success Response (200)**:
  ```json
  {
    "id": 1,
    "emt_id": 1,
    "patient_info": "...",
    "audio_file_path": "...",
    "transcription": "...",
    "llm_summary": "...",
    "urgency_level": "medium",
    "risk_score": 5,
    "priority_level": 3,
    "chief_complaint": "...",
    "vital_signs": "...",
    "symptoms": "...",
    "recommended_actions": "...",
    "critical_info": "...",
    "status": "completed",
    "created_at": "...",
    "updated_at": "...",
    "emt_first_name": "...",
    "emt_last_name": "..."
  }
  ```
- **Error Responses**:
  - 404: `{"error": "Recording not found"}`
  - 500: `{"error": "Server error getting recording"}`
- **Checklist**:
  - [x] JOINs with users to get emt_first_name, emt_last_name
  - [x] Returns 404 if not found
  - [x] Accessible to any authenticated user (no role restriction)

### GET /api/doctors/available
- **Auth**: None required (public endpoint)
- **Role**: None
- **Request**: None
- **Success Response (200)**:
  ```json
  [
    {
      "id": 1,
      "first_name": "...",
      "last_name": "...",
      "specialty": "...",
      "phone": "...",
      "email": "..."
    }
  ]
  ```
- **Error Responses**:
  - 500: `{"error": "Server error getting doctors"}`
- **Checklist**:
  - [x] Filters role='doctor' AND is_available=1
  - [x] Returns only specified fields
  - [x] No auth required

### GET /api/doctors/notifications
- **Auth**: Required
- **Role**: `doctor` only
- **Query Params**: `sortBy` (newest|priority|oldest, default: newest)
- **Success Response (200)**:
  ```json
  [
    {
      "id": 1,
      "recording_id": 1,
      "doctor_id": 1,
      "notification_type": "both",
      "sent_at": "...",
      "delivered": 0,
      "read_at": null,
      "response": null,
      "patient_info": "...",
      "llm_summary": "...",
      "urgency_level": "medium",
      "risk_score": 5,
      "priority_level": 3,
      "chief_complaint": "...",
      "vital_signs": "...",
      "symptoms": "...",
      "recommended_actions": "...",
      "critical_info": "...",
      "recording_time": "...",
      "emt_first_name": "...",
      "emt_last_name": "..."
    }
  ]
  ```
- **Sorting Logic**:
  - `newest`: ORDER BY n.sent_at DESC
  - `priority`: ORDER BY r.risk_score DESC, r.priority_level ASC, n.sent_at DESC
  - `oldest`: ORDER BY n.sent_at ASC
  - Default: newest
- **Checklist**:
  - [x] Complex JOIN across notifications, recordings, users
  - [x] Filters by req.user.id (doctor_id)
  - [x] Implements three sort modes exactly
  - [x] Returns delivered as 0/1 integer
  - [x] Returns null for unset fields

### GET /api/doctors/recording/:id
- **Auth**: Required
- **Role**: `doctor` only
- **Request**: Path param `id`
- **Success Response (200)**:
  ```json
  {
    "id": 1,
    "emt_id": 1,
    "patient_info": "...",
    "audio_file_path": "...",
    "transcription": "...",
    "llm_summary": "...",
    "urgency_level": "medium",
    "risk_score": 5,
    "priority_level": 3,
    "chief_complaint": "...",
    "vital_signs": "...",
    "symptoms": "...",
    "recommended_actions": "...",
    "critical_info": "...",
    "status": "completed",
    "created_at": "...",
    "updated_at": "...",
    "emt_first_name": "...",
    "emt_last_name": "..."
  }
  ```
- **Error Responses**:
  - 404: `{"error": "Recording not found"}`
  - 500: `{"error": "Server error getting recording"}`
- **Checklist**:
  - [x] Same as GET /api/recordings/:id but requires doctor role
  - [x] JOINs users table

### PATCH /api/doctors/availability
- **Auth**: Required
- **Role**: `doctor` only
- **Request Body**: `{is_available: boolean}`
- **Success Response (200)**:
  ```json
  {
    "message": "Availability updated successfully",
    "is_available": true
  }
  ```
- **Error Responses**:
  - 500: `{"error": "Server error updating availability"}`
- **DB Side Effects**: UPDATE users SET is_available WHERE id=req.user.id
- **Checklist**:
  - [x] Accepts boolean in request
  - [x] Converts to 0/1 for SQLite storage
  - [x] Returns boolean in response
  - [x] Updates only current doctor's record

### PATCH /api/doctors/notifications/:id/read
- **Auth**: Required
- **Role**: `doctor` only
- **Request**: Path param `id`
- **Success Response (200)**:
  ```json
  {
    "message": "Notification marked as read"
  }
  ```
- **Error Responses**:
  - 500: `{"error": "Server error marking notification as read"}`
- **DB Side Effects**: UPDATE notifications SET read_at=CURRENT_TIMESTAMP
- **Checklist**:
  - [x] Sets read_at to current timestamp
  - [x] Only updates if doctor_id matches req.user.id
  - [x] Simple success message

### POST /api/doctors/recordings/:id/respond
- **Auth**: Required
- **Role**: `doctor` only
- **Request Body**: `{response: string}`
- **Success Response (200)**:
  ```json
  {
    "message": "Response recorded successfully"
  }
  ```
- **Error Responses**:
  - 500: `{"error": "Server error recording response"}`
- **DB Side Effects**: UPDATE notifications SET response WHERE recording_id AND doctor_id
- **Checklist**:
  - [x] Updates response field in notifications table
  - [x] Only updates for current doctor
  - [x] Matches by recording_id and doctor_id

### POST /api/notifications/send
- **Auth**: Required
- **Role**: `emt` only
- **Request Body**: `{recording_id, doctor_id, notification_type}`
- **Success Response (200)**:
  ```json
  {
    "message": "Notification sent successfully"
  }
  ```
- **Error Responses**:
  - 404: `{"error": "Recording or doctor not found"}`
  - 500: `{"error": "Server error sending notification"}`
- **Side Effects**:
  - Sends SMS if type is 'sms' or 'both'
  - Sends email if type is 'email' or 'both'
  - Updates notification record: delivered=1
- **SMS Template**:
  ```
  🚑 EMERGENCY ALERT - Dr. {last_name}

  Patient Summary: {summary[:100]}...

  Urgency: {urgency_level}
  EMT: {emt_first_name} {emt_last_name}

  View full details at: {FRONTEND_URL}/recording/{id}

  Reply STOP to unsubscribe
  ```
- **Email Template**: HTML with urgency color coding
- **Checklist**:
  - [x] Fetches recording and doctor data
  - [x] Calls Twilio for SMS
  - [x] Calls SendGrid for email
  - [x] Updates delivered=1
  - [x] Uses exact SMS text format
  - [x] Uses FRONTEND_URL env var

### GET /api/notifications/status/:id
- **Auth**: Required
- **Role**: Any
- **Request**: Path param `id`
- **Success Response (200)**:
  ```json
  {
    "id": 1,
    "recording_id": 1,
    "doctor_id": 1,
    "notification_type": "both",
    "sent_at": "...",
    "delivered": 0,
    "read_at": null,
    "response": null
  }
  ```
- **Error Responses**:
  - 404: `{"error": "Notification not found"}`
  - 500: `{"error": "Server error getting notification status"}`
- **Checklist**:
  - [x] Returns notification record by ID
  - [x] delivered as 0/1

### POST /api/notifications/test
- **Auth**: Required
- **Role**: `emt` only
- **Request Body**: `{phone?, email?}`
- **Success Response (200)**:
  ```json
  {
    "message": "Test notification sent successfully"
  }
  ```
- **Error Responses**:
  - 500: `{"error": "Server error sending test notification"}`
- **Checklist**:
  - [x] Sends test SMS/email with hardcoded test data
  - [x] Uses same sendSMS/sendEmail functions
  - [x] Test message includes "test" indicator

## Background Processing

### processRecording(recordingId, audioFilePath)
1. **Status: processing**
   - UPDATE recordings SET status='processing'
2. **Fetch patient_info**
   - SELECT patient_info FROM recordings WHERE id=recordingId
3. **Whisper Transcription**
   - openai.audio.transcriptions.create()
   - model: "whisper-1"
   - response_format: "text"
   - Returns plain text string
4. **GPT-4 Analysis**
   - analyzeWithLLM(transcription, patient_info)
   - model: "gpt-4"
   - temperature: 0.3
   - max_tokens: 1000
   - Returns JSON with structured fields
5. **Update Recording**
   - UPDATE recordings SET transcription, llm_summary, risk_score, priority_level, chief_complaint, vital_signs, symptoms, recommended_actions, critical_info, status='completed'
   - **NOTE**: Does NOT update urgency_level (leaves DB default)
6. **Notify Doctors**
   - notifyDoctors(recordingId, summary)
7. **Error Handling**
   - On error: UPDATE recordings SET status='error'

### analyzeWithLLM(transcription, patientInfo)
- **Prompt**: Exact multi-line prompt from recordings.js
- **Response Parsing**:
  - Strip ```json``` markers if present
  - JSON.parse the cleaned response
  - On parse failure: return default values (risk_score=5, priority_level=3, urgency_level="moderate", etc.)
- **Risk/Priority Guidelines**: Preserve exact scoring examples from prompt
- **Checklist**:
  - [x] Exact prompt text preserved
  - [x] Strips ```json and ``` markers
  - [x] Returns default on JSON parse failure
  - [x] risk_score 0-10 (not 0-100)
  - [x] priority_level 1-5
  - [x] urgency_level string

### notifyDoctors(recordingId, summary)
- Queries available doctors: role='doctor' AND is_available=1
- For each doctor: INSERT INTO notifications (recording_id, doctor_id, notification_type='both')
- UPDATE recordings SET status='notified'
- **Does NOT send SMS/email** (that happens via /api/notifications/send)

## Database Schema

### SQLite Database
- **File**: `asclepius.db` (next to project root, not `shealthcare.db`)
- **Connection**: Direct SQLite3 connection

### users Table
```sql
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('emt', 'doctor')),
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  phone TEXT,
  specialty TEXT,
  is_available BOOLEAN DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### recordings Table
```sql
CREATE TABLE IF NOT EXISTS recordings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  emt_id INTEGER,
  patient_info TEXT,
  audio_file_path TEXT NOT NULL,
  transcription TEXT,
  llm_summary TEXT,
  urgency_level TEXT DEFAULT 'medium' CHECK (urgency_level IN ('low', 'medium', 'high', 'critical')),
  risk_score INTEGER DEFAULT 5 CHECK (risk_score >= 0 AND risk_score <= 10),
  priority_level INTEGER DEFAULT 3 CHECK (priority_level >= 1 AND priority_level <= 5),
  chief_complaint TEXT,
  vital_signs TEXT,
  symptoms TEXT,
  recommended_actions TEXT,
  critical_info TEXT,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'notified', 'error')),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (emt_id) REFERENCES users (id)
);
```

### notifications Table
```sql
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recording_id INTEGER,
  doctor_id INTEGER,
  notification_type TEXT NOT NULL CHECK (notification_type IN ('sms', 'email', 'both')),
  sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  delivered BOOLEAN DEFAULT 0,
  read_at DATETIME,
  response TEXT,
  FOREIGN KEY (recording_id) REFERENCES recordings (id),
  FOREIGN KEY (doctor_id) REFERENCES users (id)
);
```

### Indexes
- idx_recordings_emt_id ON recordings(emt_id)
- idx_recordings_status ON recordings(status)
- idx_notifications_doctor_id ON notifications(doctor_id)
- idx_users_role ON users(role)
- idx_users_available ON users(is_available)

### Seed Data
- INSERT OR IGNORE 4 demo users
- password_hash: bcrypt of "password123" with rounds=10
- Users: dr.smith, dr.jones (doctors), emt.wilson, emt.garcia (emts)

## Server Configuration

### Environment Variables
- JWT_SECRET: Required
- OPENAI_API_KEY: Required for processing
- TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER: Required for SMS
- SENDGRID_API_KEY, SENDGRID_FROM_EMAIL: Required for email
- PORT: Default 5000
- NODE_ENV: If 'production', serve client/build
- FRONTEND_URL: Default http://localhost:3000

### Middleware
- CORS: Enabled for all origins
- JSON body limit: 50mb
- Security headers: helmet equivalent
- Static files: `/uploads` → `uploads/` directory
- Production: serve React build from `client/build`

### Port & Startup
- Port 5000 (or PORT env var)
- Startup message: `🏥 Asclepius EMT System running on port 5000`

## Known Quirks to Preserve

1. **urgency_level NOT updated by LLM**: LLM returns it, but UPDATE query doesn't include it
2. **SMS template missing EMT names**: Template references emt_first_name/emt_last_name but recording object may not have them joined
3. **risk_score 0-10**: Not 0-100 (README is wrong)
4. **priority_level 1-5**: Lower number = higher priority
5. **Boolean fields as 0/1**: SQLite stores as integers, not true/false
6. **Error status**: Set on processing failures, not cleared automatically
7. **Background processing**: Uses in-process async, not queues
8. **Notification split**: notifyDoctors only INSERTs rows; /api/notifications/send does actual delivery

## Testing Requirements

1. **Demo User Login**: Verify all 4 demo accounts work (password: password123)
2. **EMT Upload Flow**: Upload → processing → completed → notified status transitions
3. **Doctor Notifications**: Query and filter work correctly
4. **Auth Failures**: 401 for missing token, 400 for invalid token, 403 for wrong role
5. **Role Restrictions**: EMT/doctor endpoints properly gated
6. **Static File Serving**: /uploads/{filename} accessible
7. **CORS**: Frontend can call backend from different origin
8. **Multipart Upload**: Audio files save correctly to uploads/
9. **Background Processing**: Non-blocking upload response
10. **Database Compatibility**: Existing asclepius.db should work if schema matches

## Non-Parity Items (Acceptable Differences)

1. **Multer error messages**: FastAPI multipart errors may differ slightly
2. **OpenAI SDK errors**: Error message strings may vary by SDK version
3. **Background concurrency**: Different async runtime may interleave differently
4. **Timestamp formats**: SQLite CURRENT_TIMESTAMP behavior may differ slightly
5. **Floating point precision**: Risk scores may have minor precision differences

## Validation Checklist

- [x] All endpoints documented
- [x] All request/response formats specified
- [x] All status codes listed
- [x] All database side effects documented
- [x] All background processing steps detailed
- [x] All error messages preserved
- [x] All quirks documented
- [x] Test plan defined
