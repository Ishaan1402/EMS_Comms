# 🏥 Asclepius EMT System

An AI-powered emergency triage system that enables EMTs to record patient conversations, automatically classify risk levels, prioritize cases by urgency, and instantly notify doctors for faster emergency response.

## 🐍 Python/FastAPI Backend Available

This repository includes a **Python/FastAPI backend** as an alternative to the Node/Express backend. It serves the same API to the React client.

- **Node/Express** (original): `npm start` or `node server.js`
- **Python/FastAPI**: `python3 main.py` or `./start-python.sh`

### Running the Python backend

```bash
pip install -r requirements.txt
python3 main.py            # http://localhost:5000 (override with PORT)
```

`./start-python.sh` does the same after installing dependencies, but it exits unless a `.env` file exists.

Environment variables (read from the environment or `.env`):

```env
JWT_SECRET=...
OPENAI_API_KEY=...
OPENAI_SCORING_MODEL=gpt-6-luna   # optional; risk scoring model
OPENAI_REASONING_EFFORT=none      # optional; leave blank for models without reasoning
OPENAI_TRANSCRIPTION_MODEL=gpt-transcribe   # optional; speech-to-text model
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=...
SENDGRID_API_KEY=...
SENDGRID_FROM_EMAIL=...
PORT=5000                  # optional
FRONTEND_URL=...           # optional, used in notification links
NODE_ENV=production        # optional, serves client/build
SEED_DEMO_USERS=1          # optional, creates the demo accounts on startup
```

If any of these keys are missing, the server still starts and logs a warning. Only the features that need a missing key fail:

- No `JWT_SECRET`: login, registration, and authenticated routes fail.
- No `OPENAI_API_KEY`: uploads succeed, but each recording is marked `error` and no doctors are notified.
- No Twilio or SendGrid credentials: the notification send routes return 500.

The SQLite schema is created in `asclepius.db` when the server starts. Demo users (password `password123`) are created only when `SEED_DEMO_USERS=1` is set, or when you run `python3 database.py --seed`.

Tests use a temporary database and need no API keys: `pip install -r requirements.txt pytest && pytest test_parity.py test_messages.py test_live_transcription.py test_case_model.py test_demo_replay.py`.

To replay the three-patient demo scenario against a running server, see [demo/README.md](demo/README.md). Demo accounts: `dr.smith` (General Hospital), `dr.jones` (Northside), `emt.wilson`, `emt.garcia`, `emt.lee`.

### Live case transcription

EMTs can start a **live case** from the EMT dashboard. The browser records the conversation in ~8 second segments. Each segment is a complete audio file, uploaded and transcribed on its own (`OPENAI_TRANSCRIPTION_MODEL`, default `gpt-transcribe`). Every segment is timestamped, stored in SQLite under its case, and pushed to the Doctor Dashboard's **Live Cases** panel as soon as it is uploaded and again when its transcript is ready.

- **Storage**: `cases` and `transcript_segments` tables. An EMT can have only one active case at a time.
  - A segment's `seq` (assigned by the client, unique per case) sets the chronological order.
  - Its `client_id` identifies the recorded clip: a re-upload of the same clip returns the stored row, while a different clip that reuses a taken `seq` gets a 409.
  - `recorded_at` is when the EMT started recording, corrected to the server clock using the upload's `sent_at`.
  - Every row has an `updated_at`, and clients keep the newest version of each row. That way a slow fetch never overwrites a newer pushed update.
  - Audio is saved under `private_uploads/` with random file names and is never served over HTTP.
  - `case_findings.segment_id` is reserved so AI-extracted findings can point back to the segment they came from.
- **Push**: Server-Sent Events at `GET /api/cases/events`, authenticated with the normal `Authorization: Bearer` header. Doctors receive every case; EMTs receive only their own. Each (re)connect starts with a `ready` event, and clients re-fetch state at that point, because events sent while a client is disconnected are not replayed. The broker runs in-process, so run a single server worker (the default).
- **Failures**: on startup, segments left pending by a restart are marked failed. Timeouts, network errors, rate limits and 5xx responses are retried once on the server. After that the segment is marked `failed` with a short reason, which both dashboards show, and the EMT can retry it (`POST /api/cases/:id/segments/:segmentId/retry`). Segments still pending after 20s are flagged as delayed, and an open case with no audio for 2 minutes stops showing as LIVE. The EMT client retries failed uploads with backoff and keeps the audio until it is accepted. If a page refresh leaves a case open, the EMT can resume recording or end the case.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/cases` | EMT | Start a case (see [Cases](#cases-one-per-patient-transport); 409 if one is already open) |
| `GET /api/cases[?status=active\|closed][&hospital_id=N]` | EMT (own) / Doctor (all) | List cases |
| `GET /api/cases/:id` | EMT (own) / Doctor | Case details |
| `POST /api/cases/:id/close` | EMT | End the case |
| `GET /api/cases/:id/segments` | EMT (own) / Doctor | Transcript history, ordered by `seq` |
| `POST /api/cases/:id/segments` | EMT | Upload a segment (multipart: `audio`, `seq`, `client_id`, `recorded_at`, `sent_at`, `duration_ms`) |
| `POST /api/cases/:id/segments/:segmentId/retry` | EMT | Re-run a failed transcription |
| `GET /api/cases/events` | EMT / Doctor | SSE stream: `ready`, `case.opened`, `case.updated`, `segment.created`, `segment.updated`, `update.created` |

Live cases are implemented only in the Python backend.

### Cases: one per patient transport

The case is the main object. One transport is one case, and everything about the patient belongs to it: transcript segments, typed updates, vital signs, AI assessments, messages and hospital acknowledgments. Nothing is overwritten, so history such as SpO2 96 → 91 → 86 is kept.

- **Creating**: `POST /api/cases` takes `patient_info`, `destination_hospital_id` (from the public `GET /api/hospitals`), `ems_unit`, and `eta_minutes`. All are optional for the API; the EMT form requires a destination. On an empty database three demo hospitals are created.
- **Who sees what**: EMTs see their own cases. Hospital users see only cases routed to their hospital (`users.hospital_id`, chosen at sign-up; `GET /api/hospitals/mine`), across case details, the live event stream and case messages. Anyone else gets a 404. A hospital user with no hospital sees no cases.
- **Updates**: `POST /api/cases/:id/updates` (EMT, open case only) adds `{"kind": "note"|"correction", "body"}`, `{"kind": "vitals", "vitals": {"spo2": 91, "hr": 110}, "body"?}` or `{"kind": "eta", "eta_minutes"}`. A correction is a new row; the original stays. An optional `client_id` makes retries idempotent. `GET /api/cases/:id/updates` and `GET /api/cases/:id/vitals` return the history.
- **`info_version`**: goes up by one whenever new patient information arrives (creation, a note, vitals, a correction, or a transcribed segment with text). ETA changes are logistics and don't change it.
- **Two kinds of status, kept apart** (KAN-11):
  - `operational_status`: `inbound` → `acknowledged` (a hospital user has seen the latest `info_version`) → `arrived` → `closed`. `POST /api/cases/:id/acknowledge` (hospital user, optional `{"info_version": n}` for the version their screen showed) and `POST /api/cases/:id/arrive` (EMT or hospital user). New information makes an acknowledged case `inbound` again.
  - `processing`: `pending` | `processing` | `completed` | `failed` | `needs_review`, with `needs_review: true` and plain-language `reasons` whenever a person should look: a failed transcription, a failed assessment, or an assessment that couldn't decide.
- **Failed audio**: a segment that can't be transcribed flags the case until it's retried (`POST /api/cases/:id/segments/:segmentId/retry`) or marked handled (`.../dismiss`, EMT or hospital user), for example after the crew re-sent it as text. A dismissed failure stays on record with who dismissed it and when.
- **AI assessments** (`case_assessment.py`): the model gets the evaluation harness's prompt (`evals/prompts/awareness_v2.txt`) and input fields, and must answer in the evaluation contract's five fields: `summary`, `meaningful_change`, `change_explanation`, `missing_information` and `preparation_category` (Prepare now / Can wait / Routine / Cannot assess). So what `evals/` measures is what the app runs.
  - "Earlier" information is what the hospital had acknowledged, so `meaningful_change` answers "what changed since you last looked". Before any acknowledgment everything is current, as in a first report.
  - `Cannot assess` (the prompt's "Unsure") and unreadable answers mark the case Needs Review. Nothing is filled with a default.
  - Every attempt is a row in `risk_assessments` with the `info_version` it read, its `baseline_version`, `input_tokens`, `output_tokens`, `latency_ms` and a list-price `cost_usd`. The case's `current_assessment` is the usable one with the highest version, so a slow result for older information never replaces a newer one. A failure keeps the previous assessment (`assessment_is_outdated: true`) and can be retried (`POST /api/cases/:id/assessments/retry`). `GET /api/cases/:id/assessments` lists every attempt.
  - Assessment waits 2 s first, so a report arriving as several segments is assessed once, whole; information arriving during a run is assessed once more afterwards. On startup, open cases whose current information has no usable assessment are re-assessed.
  - Models: `OPENAI_SCORING_MODEL` (default `gpt-6-luna`; `gpt-4` shuts down 2026-10-23) and `OPENAI_TRANSCRIPTION_MODEL` (default `gpt-transcribe`). The older single-recording flow still uses its own prompt and 0–10 risk scale.
- **Messages**: each case has its own thread at `/api/cases/:id/messages` (same API as below).

Existing databases are migrated on startup: new columns are added, and `messages` is rebuilt so a message can belong to a case or a recording.

### Case messaging (EMT ↔ hospital team)

Each case has a message thread at `/api/cases/:id/messages`: the EMT who owns the case and hospital users can use it. Recordings from the older single-recording flow keep their own threads at `/api/recordings/:id/messages`, for the EMT who recorded it and every doctor notified about it. Anyone else gets a 404. Messages live in the `messages` table (SQLite in WAL mode), ordered by id; each belongs to exactly one case or recording.

- `GET /api/recordings/:id/messages?after_id=N` returns the history, oldest first
- `POST /api/recordings/:id/messages` with `{ "body": "...", "client_id": "optional-uuid" }` sends a message. The case comes only from the URL. A retry with the same `client_id` returns the original message instead of a duplicate.
- `GET /api/recordings/:id/messages/stream?after_id=N` is a server-sent event stream of new messages. It honours `Last-Event-ID` and needs the usual `Authorization` header.

Messaging is implemented in the Python backend only.

---

## ✨ Features

### For EMTs/Paramedics
- **Audio Recording**: Record patient conversations directly from mobile devices
- **Patient Information**: Add context and details about the emergency
- **AI Processing**: Automatic transcription and medical summary generation
- **Real-time Updates**: Track processing status and urgency levels

### For Doctors
- **Instant Notifications**: Receive SMS/email alerts for new cases
- **AI Summaries**: Get structured patient information and urgency assessment
- **Response System**: Provide medical guidance and instructions
- **Case Management**: View and manage all incoming emergency cases

### Technical Features
- **Secure Authentication**: JWT-based user management with role-based access
- **Real-time Processing**: OpenAI speech-to-text for transcription + an OpenAI model for assessment
- **Multi-channel Notifications**: SMS (Twilio) + Email (SendGrid) integration
- **Responsive Design**: Mobile-first interface for field use
- **HIPAA Compliant**: Secure data handling and storage
- **SQLite Database**: Simple, file-based database for easy deployment

## 🏗️ Architecture

```
EMT Phone → Audio Recording → Backend API → Transcription → AI Assessment → Hospital Dashboard
    ↓              ↓              ↓              ↓              ↓              ↓
Web Interface → File Upload → SQLite Database → Transcription → Medical Summary → SMS/Email
```

## 🚀 Quick Start

### Prerequisites
- Node.js 16+ 
- OpenAI API key
- Twilio account (for SMS)
- SendGrid account (for email)

### 1. Clone & Install
```bash
git clone <repository-url>
cd shealthcare
npm install
cd client && npm install
```

### 2. Environment Setup
```bash
cp env.example .env
# Edit .env with your API keys
```

### 3. Start Development
```bash
# Terminal 1: Backend
npm run dev

# Terminal 2: Frontend
cd client && npm start
```

### 4. Access the App
- **Frontend**: http://localhost:3000
- **Backend**: http://localhost:5000
- **Demo Accounts**: See login page for test credentials
- **Database**: Automatically created as `shealthcare.db`

## 🔧 Configuration

### Environment Variables
```env
# JWT Secret
JWT_SECRET=your-super-secret-jwt-key-here

# OpenAI API
OPENAI_API_KEY=your-openai-api-key-here

# Twilio (SMS)
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_PHONE_NUMBER=+1234567890

# SendGrid (Email)
SENDGRID_API_KEY=your-sendgrid-api-key
SENDGRID_FROM_EMAIL=noreply@shealthcare.com
```

## 📱 Usage

### EMT Workflow
1. **Login** with EMT credentials
2. **Record** patient conversation using microphone
3. **Add** patient information and context
4. **Upload** for AI processing
5. **Monitor** processing status and results

### Doctor Workflow
1. **Login** with doctor credentials
2. **Receive** emergency notifications
3. **Review** AI-generated patient summaries
4. **Respond** with medical guidance
5. **Track** case status and history

## 🛠️ API Endpoints

### Authentication
- `POST /api/auth/register` - User registration
- `POST /api/auth/login` - User login
- `GET /api/auth/profile` - Get user profile

### Recordings
- `POST /api/recordings/upload` - Upload audio recording
- `GET /api/recordings/my-recordings` - Get EMT's recordings
- `GET /api/recordings/:id` - Get specific recording

### Doctors
- `GET /api/doctors/notifications` - Get doctor notifications
- `GET /api/doctors/recording/:id` - Get recording details
- `PATCH /api/doctors/notifications/:id/read` - Mark notification as read
- `POST /api/doctors/recordings/:id/respond` - Send medical response

### Notifications
- `POST /api/notifications/send` - Send notification to doctor
- `GET /api/notifications/status/:id` - Get notification status

## 🔒 Security Features

- **JWT Authentication** with secure token management
- **Role-based Access Control** (EMT vs Doctor)
- **Input Validation** and sanitization
- **CORS Protection** and security headers
- **File Upload Security** with type validation
- **SQLite Query Protection** against injection attacks

## 🚀 Deployment

### Heroku
```bash
# Backend
heroku create your-app-name
git push heroku main

# Frontend
cd client
npm run build
```

### Docker
```bash
docker-compose up -d
```

### Local Development
```bash
# Simple startup
chmod +x start.sh
./start.sh
```

## 📊 Performance

- **Audio Processing**: ~30-60 seconds for typical recordings
- **LLM Analysis**: ~10-20 seconds for medical summary
- **Notification Delivery**: <5 seconds for SMS/email
- **Database Queries**: Fast with SQLite indexing

## 🔮 Future Enhancements

- **Mobile Apps**: Native iOS/Android applications
- **Real-time Chat**: Live communication between EMTs and doctors
- **Image Support**: Photo uploads for visual assessment
- **GPS Integration**: Location-based doctor matching
- **Advanced AI**: Predictive analytics and triage scoring
- **Hospital Integration**: EMR system connectivity
- **PostgreSQL Migration**: For production scaling

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Contact the development team
- Check the documentation

---

**Built with ❤️ for emergency medical professionals** 
## 🎯 **New Risk Classification Features**

### **AI-Powered Risk Assessment**
- **Risk Score (0-100)**: Automated calculation based on symptoms, vital signs, and urgency
- **Priority Level (1-5)**: Intelligent classification from critical to non-urgent
- **Smart Triage**: Cases automatically ordered from highest to lowest risk
- **Real-time Prioritization**: Doctors see most critical cases first

### **Enhanced Medical Analysis**
- **Chief Complaint**: Primary reason for emergency call
- **Vital Signs**: Comprehensive vital sign analysis
- **Symptom Assessment**: Detailed symptom categorization
- **Recommended Actions**: AI-suggested immediate interventions
- **Critical Information**: Key details for medical decision-making

### **Priority Levels**
1. **Critical (Priority 1)**: Immediate life threat, <5 minutes
2. **High (Priority 2)**: Urgent, <30 minutes
3. **Medium (Priority 3)**: Moderate urgency, <2 hours
4. **Low (Priority 4)**: Routine, <24 hours
5. **Non-urgent (Priority 5)**: Scheduled care

