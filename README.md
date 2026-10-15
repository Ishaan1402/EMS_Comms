# 🏥 Asclepius EMT System

Asclepius keeps the receiving hospital up to date while an ambulance is on the way. The crew opens a case for the patient, then talks or types. The app transcribes what they say and pulls out vitals and findings, each with the words it came from. It gives the hospital a short AI handoff saying what changed since they last acknowledged. Inbound patients are ordered by a stated rule, and the crew and the hospital can message each other.

This is a hackathon prototype that runs on fictional patients. It isn't ready for real patient data (see [Limits](#limits)). The AI's categories (Prepare now, Can wait, Routine, Cannot assess) are draft labels for clinician review, not validated triage.

[Run it](#run-it) · [Demo](#demo) · [Tests](#tests) · [Repo layout](#repo-layout) · [How it works](#how-it-works) · [API](#api) · [Limits](#limits)

## Run it

### What you need

- Python 3, with SQLite 3.35 or newer (startup recovery uses `RETURNING`). The backend tests pass on Python 3.13.
- Node.js 16 or newer, with npm, for the React client.
- An OpenAI API key for transcription and the AI steps. Without one the app still runs, and shows those steps as failed.

### Backend

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py          # http://localhost:5000
```

Settings come from the environment or a `.env` file in the repo root, for example:

```env
JWT_SECRET=any-long-random-string
OPENAI_API_KEY=sk-...
SEED_DEMO_USERS=1
```

`./start-python.sh` installs the packages and starts the server, but exits unless `.env` exists.

The database is a SQLite file, `asclepius.db`, created when the server starts. Older databases are migrated on startup: new columns are added, and `messages` is rebuilt so a message can belong to a case or a recording. Set `ASCLEPIUS_DB_PATH` to use another file, such as a fresh one per demo rehearsal.

### Client

- **Development:** `cd client && npm ci && npm start` serves http://localhost:3000 and proxies `/api` to port 5000.
- **One process:** `cd client && npm ci && CI=true npm run build`, then start the backend with `NODE_ENV=production`. It serves the built client on its own port.
- **macOS:** port 5000 belongs to AirPlay Receiver. The run sheet uses `PORT=5001` with the built client.

### Settings

| Variable | Default | Used for |
|---|---|---|
| `JWT_SECRET` | none | Signing logins. Without it, login and every signed-in route fail. |
| `OPENAI_API_KEY` | none | Transcription, the AI handoff and extraction. Without it, all three fail and show as Needs Review with the reason; typed information still works. |
| `OPENAI_TRANSCRIPTION_MODEL` | `gpt-transcribe` | Speech to text |
| `OPENAI_SCORING_MODEL` | `gpt-6-luna` | The AI handoff (`gpt-4` shuts down 2026-10-23) |
| `OPENAI_EXTRACTION_MODEL` | the scoring model | Facts and vitals from the reports |
| `OPENAI_REASONING_EFFORT` | `none` | Leave blank for models without reasoning |
| `PORT` | `5000` | Server port |
| `NODE_ENV` | unset | `production` serves `client/build` |
| `ASCLEPIUS_DB_PATH` | `asclepius.db` next to the code | Database file |
| `SEED_DEMO_USERS` | off | `1` creates the demo accounts on startup |
| `SAS_SYNC_ENABLED` and the other `SAS_*` settings | off | [SAS Viya sync](#sas-viya-sync) |

The server starts even with settings missing, and warns about the main ones.

## Demo

- **Accounts** (password `password123`): `dr.smith` (General Hospital), `dr.jones` (Northside), `emt.wilson`, `emt.garcia` and `emt.lee`. They're created only with `SEED_DEMO_USERS=1`, or by running `python3 -m backend.database --seed`.
- **[demo/README.md](demo/README.md)** has the three-patient replay, the presenter's run sheet, and how to score a run against the clinician's answers.

### Synthetic clinician-review pilot

The [simplified clinician guide and annotation notebook](docs/clinician-annotation/README.md) include live Google Doc/Sheet links, a one-page PDF guide, and an XLSX notebook. The 10 reports are fictional AI-authored fixtures; clinician answers are not prefilled. These materials support a hackathon evaluation pilot, not clinical validation.

The [evaluation pipeline](evals/README.md) and [evaluation notebook](analytics/ems_evaluation.ipynb) import that workbook, compare model outputs with approved clinician references, and export a dashboard plus CSVs for Python/SAS. Mock mode tests the pipeline while annotations are pending; it does not report clinical performance. [Model comparison](evals/MODEL_COMPARISON.md) adds opt-in OpenAI, Claude, Gemini, and local Ollama adapters; planning is offline and does not run inference.

## Tests

```bash
.venv/bin/pip install -r requirements.txt                          # includes pytest
.venv/bin/python -m pytest tests                                  # backend
cd client && CI=true npm test -- --watchAll=false --runInBand      # frontend
```

- Backend tests use temporary databases and need no API keys. The evaluation contract and pilot fixtures are included. Install `evals/requirements.txt` for optional workbook import, then run `python -m pytest evals/tests` too.
- The backend began as a port of a Node/Express server, which has since been removed. `tests/test_parity.py` still checks the API contract the port kept, such as error codes and messages.

## Repo layout

- `main.py`: starts the server (`.venv/bin/python main.py`).
- `backend/`: the case model, AI steps, database, FHIR and SAS exports, and the API in `backend/routes/`.
- `client/`: the React app.
- `tests/`: the backend tests.
- `demo/`: the replay and run sheet. `evals/`: the evaluation prompts. `sas/`: SAS Viya scripts and the CAS notebook. `docs/`: design notes.

## How it works

1. **The EMT opens a case:** the destination hospital, the unit, an ETA and a short summary.
2. **The crew talks or types.** The hospital sees live draft words while the crew records a report (up to two minutes). Send uploads the uninterrupted recording for the completed transcript and AI handoff. Typed notes, vitals, corrections and ETA changes go in as updates.
3. **Each new piece of patient information gets a version number.** The AI writes a short handoff that compares it with what the hospital last acknowledged. A separate step pulls out vitals, findings and treatments, each quoting the words it came from.
4. **The hospital** sees its inbound patients in an order set by a stated rule, acknowledges what it has seen, and can message the crew.
5. **Nothing is overwritten:** a correction is a new row. Case data can be exported to SAS Viya without any free text.

A real-model run on Oct 11 measured, on the server, about 1 s from a clip arriving to its transcript being pushed, and about 4 s to the AI handoff. For how the code is organized, its limits and the planned changes, see [docs/architecture-review.md](docs/architecture-review.md).

### Live case transcription

The EMT records one report at a time (Record Report, then Send Report). The hospital sees draft transcripts from roughly eight-second clips as the crew speaks. Those words are labelled **Live draft** and may change on Send. At the same time, one uninterrupted MediaRecorder captures the complete report. Send uploads that recording for a fresh transcription (`OPENAI_TRANSCRIPTION_MODEL`); only this completed transcript enters the AI handoff, extraction, acknowledgment and FHIR export. The completed transcript replaces drafts in the normal view; the earlier drafts remain available for inspection.

This keeps early visibility while avoiding fixed audio cuts in the transcript used by the AI. It does not establish speech accuracy; check the transcript and correct it when needed. The two-minute limit stops recording and asks the crew to Send. Typed updates and crew alerts remain immediate. Complete audio awaiting upload stays in this page's memory: keep the page open until delivery completes. Draft uploads are best effort and cannot block the complete recording.

- **Storage:** the `cases` and `transcript_segments` tables. An EMT can have only one open case at a time.
  - A clip's `seq` is assigned by the client, is unique per case, and sets the order.
  - Its `client_id` identifies the recording. Re-uploading the same clip returns the stored row; a different clip that reuses a taken `seq` gets a 409.
  - `recorded_at` is when the EMT started recording, corrected to the server clock using the upload's `sent_at`.
  - Every row has an `updated_at`, and clients keep the newest version of each row, so a slow fetch never overwrites a newer pushed update.
  - `is_preview=1` identifies a live draft. It never receives an information version or supplies model inputs. SAS event details identify these as live drafts.
  - Audio is saved under `private_uploads/` with random file names and is never served over HTTP.
- **Reports** (`backend/case_reports.py`): the clips between Record Report and Send Report form one report, and `POST /api/cases/:id/reports/:report_id/end` with the non-draft clip count closes it (normally one for a microphone report). The case gets new information and a new AI handoff once the sent report is transcribed. Older multi-clip clients and the replay still show "Reporting…" until their report completes. A failed Send remains visible and can be retried with the same report id and clip count. The send may arrive before its clips, sending twice is harmless, and closing the case or marking arrival ends an unsent report. A clip uploaded without a report id is a report of its own.
- **Push:** Server-Sent Events at `GET /api/cases/events`, authenticated with the normal `Authorization: Bearer` header.
  - Hospital users receive the cases routed to their hospital; EMTs receive only their own.
  - Each (re)connect starts with a `ready` event, and clients re-fetch state at that point, because events sent while a client is disconnected aren't replayed.
  - The stream ends with `auth.expired` when the login expires or the user's role or hospital changes.
  - The broker runs in-process, so run a single server worker (the default).
- **Failures:**
  - On startup, clips left pending by a restart are marked failed.
  - Timeouts, network errors, rate limits and 5xx responses are retried once on the server. After that, the clip is marked `failed` with a short reason, which both dashboards show. The EMT can retry it, and either side can mark it handled.
  - Clips still pending after 20 s are flagged as delayed. An open case with no audio for 2 minutes stops showing as LIVE.
  - The EMT client retries failed uploads with backoff and keeps the audio in the open page until it's accepted; a page reload loses audio that wasn't uploaded.
  - If only drafts reached the server before a refresh, arrival or closure, the case shows that the complete audio is missing. The draft is never silently treated as a completed report; re-record or send a typed update, then mark the failure handled.

### Cases: one per patient transport

The case is the main object. One transport is one case, and everything about the patient belongs to it: clips, typed updates, vital signs, AI assessments, messages and hospital acknowledgments. Nothing is overwritten, so history such as SpO2 96 → 91 → 86 is kept.

- **Creating:** `POST /api/cases` takes `patient_info`, `destination_hospital_id` (from the public `GET /api/hospitals`), `ems_unit` and `eta_minutes`. All are optional for the API; the EMT form requires a destination. On an empty database, three demo hospitals are created.
- **Who sees what:** EMTs see their own cases. Hospital users see only cases routed to their hospital (`users.hospital_id`, chosen at sign-up; `GET /api/hospitals/mine`), across case details, the live event stream and case messages. Anyone else gets a 404. A hospital user with no hospital sees no cases.
- **Updates:** `POST /api/cases/:id/updates` (EMT, open case only) takes one of:
  - `{"kind": "note"|"correction", "body"}`. A correction is a new row; the original stays.
  - `{"kind": "vitals", "vitals": {"spo2": 91, "hr": 110}, "body"?}`
  - `{"kind": "eta", "eta_minutes"}`

  An optional `client_id` makes retries idempotent. `GET /api/cases/:id/updates` and `GET /api/cases/:id/vitals` return the history.
- **`info_version`** goes up by one whenever new patient information arrives: a note, vitals, a correction, or a sent report with text. Opening the case is version 1. ETA changes are logistics and don't change it, but they do re-run the AI handoff, so it never quotes an old arrival time.
- **Two kinds of status, kept apart** (KAN-11):
  - `operational_status`: `inbound` → `acknowledged` (a hospital user has seen the latest `info_version`) → `arrived` → `closed`.
    - `POST /api/cases/:id/acknowledge` (hospital user) takes an optional `{"info_version": n}`, the version their screen showed.
    - `POST /api/cases/:id/arrive` is for the EMT or a hospital user.
    - New information makes an acknowledged case `inbound` again.
  - `processing`: `pending` | `processing` | `completed` | `failed` | `needs_review`. Whenever a person should look, it sets `needs_review: true` with plain-language `reasons`: a failed transcription, a failed assessment, or an assessment that couldn't decide.
- **Failed audio:** a clip that can't be transcribed flags the case until it's retried (`POST /api/cases/:id/segments/:segmentId/retry`) or marked handled (`.../dismiss`, EMT or hospital user), for example after the crew re-sent it as text. A dismissed failure stays on record, with who dismissed it and when.
- **AI assessments** (`backend/case_assessment.py`):
  - **Contract.** The model gets the evaluation harness's prompt (`evals/prompts/awareness_v3.txt`; v2 is kept for comparison) and input fields. It must answer in the evaluation contract's five fields: `summary`, `meaningful_change`, `change_explanation`, `missing_information` and `preparation_category` (Prepare now / Can wait / Routine / Cannot assess). So what the evaluation measures is what the app runs. `tests/test_case_model.py` checks the contract against the included `evals/contracts.py`.
  - **What "earlier" means.** Earlier information is what the hospital had acknowledged, so `meaningful_change` answers "what changed since the earlier acknowledged report". Before any acknowledgment, everything counts as current, as in a first report. Acknowledging an earlier version while newer information exists re-assesses the case against it. Information is put in the order it was spoken or typed; text with no recorded version (from before upgrading) counts as not yet seen.
  - **Cannot assess.** `Cannot assess` (the prompt's "Unsure") marks the case Needs Review. An answer that doesn't follow the format is a retryable failure, and the last usable assessment stays current. Nothing is filled with a default.
  - **Every attempt is kept.** Each is a row in `risk_assessments` with the `info_version` it read, its `baseline_version`, the ETA it saw (`input_eta_at`), `input_tokens`, `output_tokens`, `latency_ms` and a list-price `cost_usd`. The case's `current_assessment` is the usable one with the highest version, so a slow result for older information never replaces a newer one. A failure keeps the previous assessment (`assessment_is_outdated: true`) and can be retried (`POST /api/cases/:id/assessments/retry`). `GET /api/cases/:id/assessments` lists every attempt.
  - **Timing.** The assessment first waits 2 s, plus up to 60 s for clips still being transcribed. Reports that haven't been sent are skipped, so a handoff is never built from half a report. Information arriving during a run is assessed once more afterwards. On startup, open cases whose current information has no usable assessment are re-assessed.
  - **Models:** `OPENAI_SCORING_MODEL` and `OPENAI_TRANSCRIPTION_MODEL`.
- **Patient information** (`backend/case_extraction.py`, KAN-8) is its own pipeline step, next to the assessment: audio → transcript → patient information. It runs, fails and is retried on its own.
  - **What it pulls out:** the chief complaint; vitals (added to the case's vital-sign trend next to typed ones, with BP as 110/70 and "on room air" kept); findings (present / absent / not assessed); treatments (given / planned).
  - **Where each fact came from:** the clip or typed note (`segment_id` / `update_id`), with the exact words, which the app checks against that source. Hover a value on the dashboard to see them.
  - **What each run reads:** the information since the last completed run, with earlier text only as context.
  - **Records:** `extraction_runs` (tokens, latency, cost, failure) and `case_findings`.
  - **API:** `GET /api/cases/:id/findings`, `GET /api/cases/:id/extractions` (every attempt) and `POST /api/cases/:id/findings/retry`. Changes are pushed as `findings.updated` events.
  - **The assessment still reads the reports themselves.** That's the contract the evaluation measures; feeding it extracted facts would need its own evaluation first.
  - **Model:** `OPENAI_EXTRACTION_MODEL`, defaulting to the scoring model.
- **Crew alerts** (`backend/case_alerts.py`, `backend/routes/alerts.py`): the crew can call Stroke (last known well), STEMI (symptom onset), Trauma (time of injury) or Sepsis (symptom onset), with that key time or "not stated", change it, or cancel it. The hospital acknowledges alerts separately from the reports, and an optional sound plays only when a crew calls one. Alerts never go into the AI's input.
- **Hospital queue** (`backend/case_queue.py`, rule draft-4, not yet clinician-approved):
  - `GET /api/cases/queue` orders the hospital user's inbound cases on the server, with the reasons for each position: unacknowledged information, an unacknowledged crew alert or a processing failure first, then active crew alerts, then the earliest expected arrival. **By default the AI's preparation category is shown as "AI suggests" but never changes the order**, which keeps ranking patients with the people who know them (see the FDA's clinical decision support guidance).
  - **Order modes** (`PUT /api/hospitals/mine/queue-mode`, per hospital): **Standard** is the rule above (the default), **AI-assisted** (`ai-assisted-1`, `case_queue.ai_assisted_ordered`) adds the AI's category after the crew alerts and before arrival time, and **Manual** shows the doctors' own order. A crew's call and unacknowledged information outrank the AI in AI-assisted, and the response carries `queue_mode`, `rule_versions` and `suggested_position` (where Standard would put each case). Queue history and the SAS export record the Standard order in every mode.
  - **The order depends only on stored state, never on the clock,** so it changes only when something is committed.
  - **Every change is recorded** (`backend/queue_history.py`, KAN-29) as a revision. Each revision lists who entered, moved or left, the cause, and whose case caused it.
  - **Reading the queue never records anything.**
  - `GET /api/cases/queue/history` returns the history, and the SAS export has it as `ASC_QUEUE`.
  - **Arrival highlight** (KAN-34): `PATCH /api/hospitals/mine` sets how early a patient is flagged "Arriving soon", from 1 to 60 minutes (default 5) per hospital. It only changes that badge, never the AI or the order.
- **Export for the hospital's record** (`backend/fhir_export.py`): `GET /api/cases/:id/fhir` returns the handoff as a FHIR R4 Bundle: patient, encounter, the reports, vital signs, treatments, and a Provenance for each AI-extracted fact that holds the crew's exact words and the model version. The AI's category is left out. It validates against the `fhir.resources` R4B models but hasn't been tried with an EHR.

### Case messaging (EMT ↔ hospital team)

Each case has a message thread. It's open to the EMT who owns the case and to the users of its destination hospital; anyone else gets a 404. Messages live in the `messages` table (SQLite in WAL mode), ordered by id.

- `GET /api/cases/:id/messages?after_id=N` returns the history, oldest first, up to 500 at a time.
- `POST /api/cases/:id/messages` with `{"body": "...", "client_id": "optional-uuid"}` sends a message. A retry with the same `client_id` returns the original message instead of a duplicate.
- `GET /api/cases/:id/messages/stream?after_id=N` is a Server-Sent Events stream of new messages. It honours `Last-Event-ID` and needs the usual `Authorization` header.

### SAS Viya sync

`backend/sas_sync.py` pushes the app's case data to CAS tables in SAS Viya, for evaluation and Visual Analytics.
- With `SAS_SYNC_ENABLED=1`, the server rebuilds five tables every 10 s and replaces any that changed: it uploads to a staging table, swaps it in, and saves to disk.
- SAS is never in a request's path. Failures are retried with backoff and shown at `GET /api/sas/status` (hospital users). `?check=true` also signs in to SAS and reads the CAS server.

| Table (`SAS_CASLIB`, default `Public`) | One row per |
|---|---|
| `ASC_CASES` | case, current state (transport status, AI processing, category, acknowledgment) |
| `ASC_EVENTS` | thing that happened: case started/arrived/closed, update, clip uploaded/transcribed/failed/dismissed, assessment, patient information extraction, acknowledgment, message, crew alert called/cancelled/acknowledged and its key time, queue move (with its cause) |
| `ASC_ASSESSMENTS` | AI assessment attempt, with tokens, latency and cost |
| `ASC_QUEUE` | change to a hospital's queue order: a patient entering, moving or leaving, with positions before and after, the cause (e.g. `report_completed`, `acknowledged`, `alert_called`) and whose case caused it |
| `ASC_VITALS` | vital-sign reading, typed or extracted from a report (`SOURCE`: typed vitals / radio / typed note / case opened) |

- **Joins:** rows carry `CASE_ID`, `SOURCE_CASE_ID` and `SOURCE_RUN_ID`, so they join with the evaluation datasets.
- **Timestamps:** each comes as `*_AT` (ISO UTC text) and `*_DT` (SAS datetime).
- **No free text leaves the app:** transcripts, notes, messages and AI summaries stay in SQLite.

Commands:
- `python -m backend.sas_sync check`: sign in and read the CAS server and caslibs (no writes).
- `python -m backend.sas_sync push`: push every table now.
- `python -m backend.sas_sync csv sas_export/`: write the tables as CSV files plus `schema.json`; no SAS needed.

Other settings: `SAS_VIYA_URL`, `SAS_CLIENT_ID`, `SAS_CLIENT_SECRET`, `SAS_CAS_SERVER` (default `cas-shared-default`), `SAS_TABLE_PREFIX` (`ASC_`), `SAS_SYNC_INTERVAL_SECONDS` (`10`) and `SAS_SAVE_TABLES` (`1`).

Sign-in uses an OAuth client with the client_credentials grant. That client needs permission to run CAS actions and to write tables in the caslib. Until it has that, export the CSVs and load them as yourself from Jupyter in SAS Viya with `sas/load_csvs.py`. It creates the same tables with the same column types, so dashboards keep working when the live push takes over.

## API

EMTs act on their own cases; hospital users act on cases routed to their hospital.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/auth/register`, `POST /api/auth/login` | anyone | Accounts. Hospital users pick their hospital at sign-up. |
| `GET /api/auth/profile` | signed in | The signed-in user |
| `GET /api/hospitals` | anyone | Hospitals, for sign-up and the EMT's destination list |
| `GET /api/hospitals/mine`, `PATCH /api/hospitals/mine` | signed in; PATCH hospital user | The user's hospital and its arrival highlight |
| `POST /api/cases` | EMT | Start a case (409 if one is already open) |
| `GET /api/cases[?status=active\|closed][&hospital_id=N]` | EMT / hospital user | List cases: open first, then newest, up to 50 |
| `GET /api/cases/:id` | EMT / hospital user | Case details |
| `POST /api/cases/:id/close` | EMT | End the case |
| `POST /api/cases/:id/arrive` | EMT / hospital user | Mark the patient handed over |
| `POST /api/cases/:id/acknowledge` | hospital user | Acknowledge the information up to a version |
| `POST /api/cases/:id/updates` | EMT | Typed note, vitals, correction or ETA |
| `GET /api/cases/:id/updates`, `GET /api/cases/:id/vitals` | EMT / hospital user | Update and vital-sign history |
| `POST /api/cases/:id/segments` | EMT | Upload a clip (multipart: `audio`, `seq`, `client_id`, `recorded_at`, `sent_at`, `duration_ms`) |
| `GET /api/cases/:id/segments` | EMT / hospital user | Transcript, ordered by `seq` |
| `POST /api/cases/:id/segments/:segmentId/retry` | EMT | Re-run a failed transcription |
| `POST /api/cases/:id/segments/:segmentId/dismiss` | EMT / hospital user | Mark a failed clip as handled |
| `GET /api/cases/:id/assessments`, `POST /api/cases/:id/assessments/retry` | EMT / hospital user | Every AI handoff attempt, each marked `is_current`; retry a failed one |
| `GET /api/cases/:id/findings`, `GET /api/cases/:id/extractions`, `POST /api/cases/:id/findings/retry` | EMT / hospital user | Extracted patient information and every attempt; retry a failed one |
| `GET /api/cases/:id/messages`, `POST /api/cases/:id/messages`, `GET /api/cases/:id/messages/stream` | EMT / hospital user | Case messages |
| `POST /api/cases/:id/reports/:reportId/end` | EMT | Close a report (`{"clips": N}`) |
| `GET /api/cases/:id/alerts`, `POST /api/cases/:id/alerts` | EMT / hospital user; POST EMT | Crew alerts: history; call, change or cancel |
| `POST /api/cases/:id/alerts/acknowledge` | hospital user | Acknowledge the crew's alerts |
| `GET /api/cases/:id/fhir` | EMT / hospital user | The handoff as a FHIR R4 Bundle |
| `GET /api/cases/queue`, `GET /api/cases/queue/history[?after_revision=N]` | hospital user | Inbound queue with reasons, and its history |
| `GET /api/cases/events` | EMT / hospital user | Live events (SSE): `ready`, `case.opened`, `case.updated`, `segment.created`, `segment.updated`, `update.created`, `findings.updated`, `hospital.updated`, `auth.expired` |
| `GET /api/sas/status[?check=true]` | hospital user | State of the SAS sync |
| `GET /api/version` | signed in | The running build: code revision, client bundle, models, prompt and queue rule versions |

## Limits

- **Not for real patient data.**
  - Anyone can sign up as a hospital user for any hospital.
  - Logins last 24 hours in browser storage.
  - Audio is stored unencrypted, with no retention policy.
  - CORS allows any origin.
- **One server process.** Live events and the AI steps run in memory, so run a single worker.
- **Auto-reload in development.** `python3 main.py` reloads when a `.py` file changes, unless `NODE_ENV=production` (the demo setting), so don't develop in the checkout that serves a demo without it.
- **Unsent audio lives only in the open page.** Audio waiting to upload is lost if the EMT's page reloads or closes.

[docs/architecture-review.md](docs/architecture-review.md) has the planned changes for each of these.

## License

MIT. See [LICENSE](LICENSE).
