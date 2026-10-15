# Architecture review and recommended changes

13 October 2026. Reviewed `main` at `2dc2a15`, with #32, #33 and #34 open and held until after the demo. Line numbers refer to `main` at that commit.

**Status.** This is a write-up, not a change. Only the README rewrite and the requirements fix in the same branch are done. Everything else here is a suggestion, with enough design to build from.

**Scope.** Sized for the hackathon. Provisioning, sign-up rules and HIPAA are out of scope for now; the minimum before real patient data is listed under [Before real patient data](#before-real-patient-data).

## Summary

The engineering is careful. History is never overwritten, the hospital acknowledges an exact version of the information, writes can be retried safely, failures are shown instead of filled in, and every extracted fact quotes the words it came from. Keep all of that.

The biggest problem is in the product model, not the code. **New information is counted per 8-second clip, not per report.** While a medic is talking, the case falls back to "not acknowledged" every few seconds, jumps up the queue, and gets a new AI handoff built from half a report. The scripted demo hides this, because it uploads each report's clips in one burst. A live microphone shows it.

The architecture is fine for a hackathon. The cost is concentrated in three places:
- `routes/cases.py` (1,157 lines) holds the HTTP routes, the case read model, transcription and the live stream, and `sas_sync.py` imports its read model from there.
- Two rules are written out several times: "is the AI assessment current?" (five copies) and "who can see this case?" (four).
- Chat has its own live channel next to the case event stream.

| # | Change | Size | Touches Anand's screens | Status |
|---|---|---|---|---|
| 1 | Requirements fix and README rewrite | S | No | Done in this branch |
| 2 | One rule each for assessment-current and access; split `routes/cases.py` | M | No | [Documented](#architecture-changes), not done |
| 3 | [Explicit reports](#1-explicit-reports) | L | Yes | Design below |
| 4 | [Age, sex and crew alerts](#2-age-sex-and-crew-alerts) and [hospital sound alerts](#3-hospital-alerts) | M | Yes | Design below |
| 5 | [Offline-safe uploads](#4-offline-safe-uploads-and-screen-wake-lock) and a screen wake lock | M | Yes | Design below |
| later | One live channel, old routes, parity error codes, database off the event loop | — | Some | With the held old-routes PR (KAN-15) |

Step 2 comes before 3–5 because those features land in the code it moves. Step 3 comes before 5 because the offline queue also carries the end-of-report request.

## How the app works today

```
EMT browser                    Server: one FastAPI process + SQLite                 Hospital browser

open case ───────────────────▶ case created (info_version 1)
clip every ~8 s ─────────────▶ clip stored ─▶ transcribed ──┐
typed note / vitals ─────────▶ update stored ───────────────┼─▶ info_version + 1
ETA change ──────────────────▶ eta_at changed (no version)  │        │
                                                            │        ├─▶ extraction: facts, each quoting its source
                                                            │        └─▶ assessment: handoff compared with the
                                                            │            last acknowledged version
                                                            ▼
                               every committed change ─▶ event on /api/cases/events ──────▶ queue + case view
                                                     └─▶ hospital queue re-sorted,
                                                         moves recorded (queue_events)
                                                                         ◀──────────── acknowledge version n
```

The ideas everything rests on:
- **One case per transport.** Clips, typed updates, vitals, assessments, acknowledgments and messages all hang off it. Nothing is overwritten: a correction is a new row.
- **`info_version` counts patient information.** Opening the case is version 1. Each typed note, vitals entry or correction adds one, and so does each clip that comes back with words (`routes/cases.py:1103`). ETA changes don't.
- **Acknowledgment is by version.** Anything newer than the acknowledged version makes the case "not acknowledged" again.
- **Assessments are versioned.** Each attempt records the version it read, the acknowledged version it compared against and the ETA it saw. The current assessment is the newest usable one, so a slow answer never replaces a newer one.
- **The queue order comes from stored state through a stated rule** (`case_queue.py`, draft-3). Every change in order is recorded with its cause (`queue_history.py`).
- **Every write the EMT makes can be retried safely.** Clips, updates and messages carry client ids; close and arrive do nothing when repeated.
- **Startup recovers interrupted work.** Clips and assessments left in flight are marked failed, and open cases without a current assessment are re-assessed.
- **Everything live runs in one process:** the event broker (`realtime.py`), the assessment and extraction runners, and the chat listeners. Run one server worker.

### Where the code is

| File | Lines | What it does |
|---|---|---|
| `routes/cases.py` | 1,157 | Cases API, live event stream, clip upload and transcription, the case read model (`CASE_SELECT`, `describe_cases`), hospital settings |
| `case_assessment.py` | 487 | AI handoff on the evaluation's prompt and contract; the per-case runner shared with extraction |
| `case_extraction.py` | 484 | Facts and vitals from the reports, each with its quote |
| `database.py` | 583 | Connection, schema, migrations, demo users |
| `sas_sync.py` | 682 | SAS Viya export (no free text leaves the app) |
| `routes/messages.py` | 326 | Chat threads for cases and old recordings, with their own polling stream |
| `case_queue.py`, `queue_history.py` | 89, 83 | Queue rule and its history |
| `realtime.py` | 70 | In-process event broker |
| `main.py` | 261 | App setup, startup recovery, error handlers kept compatible with the old Express server |
| `routes/recordings.py`, `doctors.py`, `notifications.py` | 412, 240, 321 | The first single-recording flow. No screen uses it. Until #34, `recordings.py` also holds the OpenAI client and thread pools |
| `client/src/components/LiveCaseRecorder.js` | 445 | EMT screen |
| `client/src/components/LiveCasesPanel.js` | 419 | Hospital queue and case view |
| `client/src/hooks/useSegmentRecorder.js`, `useSegmentUploads.js` | 142, 102 | 8-second clips; upload queue in memory |

### Keep these

- Append-only history and acknowledgment by exact version.
- No invented values: an unreadable model answer is a failure with a retry, never a normal-looking default.
- Each extracted fact quotes the clip or note it came from, and the quote is checked against that source.
- The assessment runs the evaluation's prompt and output contract.
- Safe retries and startup recovery.
- The SAS export carries no free text.
- 308 backend tests, plus the frontend suite. Two more skip until Vrishank's branch, with the evaluation contract and the pilot cases, is merged.

## Product changes

### 1. Explicit reports

**The case stays the main object.** A report is a new level inside it, next to typed updates. It groups the clips the medic records between **Record report** and **Send**, like one radio call.

#### Why

Each clip with words raises `info_version`, and each raise does four things: the hospital's acknowledgment goes stale, the case moves into the queue's top tier (unacknowledged information sorts first, `case_queue.py:68`), the AI re-assesses, and extraction re-runs. A typed update raises it once, because it is one deliberate message. Voice raises it every 8 seconds.

- **The evaluation measures whole reports:** an earlier report against the current one. Live, the app assesses half-reports. The 2-second settle (`case_assessment.py:69`) only gathers a whole report when all its clips arrive at once.
- **The run sheet already explains around it.** Its answer to "why does it say provisional?" is that the app can't tell when the crew has finished a radio report (`demo/README.md:80`). The "why did that patient move when the doctor acknowledged?" answer (`:85`) comes from the same mechanism.
- **The scripted demo hides it.** `demo/replay.py:266` uploads a report's clips back to back, so the settle works. With a phone, clips arrive across the whole report.

#### Design

**Concepts.** A report is open from its first clip until it ends. It ends when the EMT taps Send, when the case is closed or marked arrived, or (after a page reload) when the EMT screen ends it. It is complete once it has ended and every one of its clips has arrived and finished transcribing, successfully or not. On completion, if any clip has words, `info_version` goes up once and all the report's clips get that version. Assessment and extraction then run once, as they do after a clip today.

**Data.**
- New table `case_reports`: `id`, `case_id`, `client_id` (from the device, unique per case), `clip_count` (NULL until it ends), `started_at`, `ended_at`, `completed_at`, `info_version` (the version it created; NULL if it had no words).
- New column `transcript_segments.report_id`.
- A clip uploaded without a report id gets a one-clip report that has already ended. All clips then go through one completion path, and today's API and tests keep working.

**API.**
- Clip upload takes an optional `report_id` form field. The server creates the report row the first time it sees the id.
- `POST /api/cases/:id/reports/:report_id/end` with `{"clips": N}` (EMT, open case). The end can arrive before the clips do; the server keeps the count and waits. Repeating it with the same N returns the report; a different N is a 409.
- Close and arrive end every open report with the number of clips received so far.

An earlier version of this suggestion put an end marker on the last clip instead of sending an end request. The end request is safer: the recorder drops an empty final clip (`useSegmentRecorder.js` only hands off non-empty blobs) and the server rejects empty audio, so a marker could be lost. Uploads also run in parallel today, so the last clip isn't guaranteed to arrive last.

**Completion check.** One function, run after a clip finishes transcribing, after an end request, after close or arrive, and at startup after interrupted clips are failed:
- Ready when the report has ended, holds `clip_count` clips, none is pending, and it isn't complete yet.
- Run it in the same transaction as the clip update (`BEGIN IMMEDIATE`), so two clips finishing together complete the report once.
- A clip that finishes after its report completed, such as a retried failure, adds information on its own, as a clip does today.

**Readers.**
- `case_assessment.read_inputs` and `case_extraction.read_new_information` both skip clips of unfinished reports, so half a report never reaches the model or the context.
- Today the two readers treat a clip without a version differently: the assessment counts it as unseen (`case_assessment.py:181`), extraction as already read (`case_extraction.py:182`). Give both one shared helper that knows the three cases: unfinished report, pre-version text, versioned.

**Screens.**
- EMT: "Start case & record report", then **Send report** (stops the recorder, whose `stop()` already hands off the last partial clip, then sends the end request with the clip count) and **Record report** for the next one. End case and Mark arrived stay separate. If iOS asks for the microphone again on every report, keep the stream open between reports and only stop the recorder.
- Hospital: an open report shows as "Medic 8 reporting…" with its words appearing, but no NEW tags. On completion, the clips get their version, the NEW tags appear, and the case needs one acknowledgment.
- The case response gets an `open_report` summary (id, clips received, started).
- The "provisional" badge (`CaseOverview.js:50`) becomes "reporting", since the handoff now only covers finished reports.
- Add `report_completed` to the queue-history causes.

**Replay.** Give each `audio_report` and `garbled_audio` event a report id, upload its clips with it, then send the end request with the clip count. Update the run sheet's "provisional" answer.

**Unchanged.** The AI's input format and the evaluation contract, acknowledgment, the queue rule, and typed updates, which still raise the version immediately.

**Tests.** Today's backend tests keep passing unchanged, because clips without a report id behave as they do now. The frontend test for the "provisional" badge (`CaseOverview.test.js:21`) changes with the badge. New tests:
- The version doesn't move until the report is complete.
- The end request arrives before the clips; clips are transcribed out of order.
- An empty final clip; a failed clip inside a report.
- Close and arrive end open reports; startup completes reports that are ready.
- The end request is idempotent, and a mismatched count is a 409.
- Both readers skip unfinished reports, and the queue doesn't move during a report.

**To decide.**
- Push-to-talk (recommended) or a microphone that stays on, with Send marking where each report ends? The backend is the same either way.
- Should the hospital see an unfinished report's words? Recommended: yes, marked as in progress.

### 2. Age, sex and crew alerts

**Age and sex.**
- Two fields on the start form: `patient_age_years` (0–120) and `patient_sex` (`female`, `male`, `unknown`).
- Store them on the case, and change them later through a `details` update. That's the same pattern as ETA (`cases.eta_at` holds the current value, `case_updates` the history), except that this one raises `info_version`, because it is patient information.

**Alerts: stroke, STEMI, trauma, sepsis.**
- Store them as an `alert` update with a child table `alert_changes`: `update_id`, `alert`, `action` (`called` or `cancelled`) and `key_time`. This is the same shape as `vital_readings` under a vitals update.
- The case's current alerts are the latest change per alert type.
- A called alert raises `info_version`, so the hospital acknowledges it.
- The key times: last known well for stroke, symptom onset for STEMI, time of injury for trauma, optional for sepsis.

**API.**
- `POST /api/cases` takes `patient_age_years`, `patient_sex` and `alerts: [{alert, key_time}]`.
- Updates take `{"kind": "details", ...}` and `{"kind": "alert", "alert": "stroke", "action": "called", "key_time": "..."}`.
- The case response adds the current alerts with who called them and when.

**Screens.**
- The hospital row shows "67 F" and "STROKE · last known well 14:10 · 52 min ago", counting up. The time since last known well is what a stroke team works from.
- The EMT can call or cancel an alert from the update form during transport.

**Queue.** Proposed draft-4 rule: crew alerts rank above the AI's categories within the same attention tier, so a person's call outranks a draft AI label. "Crew alert: stroke" goes in the reasons. Bump `QUEUE_RULE_VERSION` so every later queue change is tagged with the new rule. This needs the clinician's agreement, like draft-3.

**AI input: leave it alone.** The spoken report usually says these anyway, and the evaluation measures the current input. Adding a typed line such as "Crew alert: stroke" is a change to measure first with Vrishank.

**Demo.** Keep `demo/three_patients.json` free of alerts. It is the clinician's queue group Q02, so its comparison (KAN-13) has to mean the same thing as before. Put alerts in a separate scenario.

**SAS (optional).** Alerts are a controlled vocabulary plus times, so they can go into `ASC_CASES` and `ASC_EVENTS` without breaking the no-free-text rule.

Expect the market research to call these table stakes; Pulsara, as far as I know, is built around exactly these alerts. They are worth doing because clinicians expect them, not because they set the product apart.

### 3. Hospital alerts

The hospital screen announces a new case with a toast and nothing else. Nobody in an ED watches a browser tab.

- **When to alert:** a new case for this hospital, a crew alert being called, and a case's AI category first becoming Prepare now. Not every report, acknowledgment or ETA change, or the alerts turn into noise.
- **How:** a small hook on the hospital screen.
  - Play a short chime with Web Audio (no sound file needed).
  - Show a browser notification when the tab is hidden.
  - Alert once per case and event, using a set kept for the session, like `announcedAck` on the EMT screen.
- **Autoplay:** browsers won't play sound until someone clicks on the page. Add an **Enable sound** toggle that also asks for notification permission and remembers the choice. Add "click Enable sound on the hospital screen" to the run sheet.

### 4. Offline-safe uploads and screen wake lock

**Today:** a clip that can't upload lives only in page memory (`useSegmentUploads.js:34`). It gets 4 tries a few seconds apart, then waits for the EMT to tap Retry. A reload, a crash, the phone discarding a background tab or logging out loses it. A one-minute dead zone means about 8 taps. The screen says "Upload failed — audio kept on this device" (`LiveTranscript.js:111`), which is only true until the page reloads.

**With an outbox:**
- **Saved first:** each clip is written to IndexedDB the moment it's recorded, and deleted only after the server confirms it (200 or 201).
- **In order, one at a time:** a single uploader drains the outbox by `seq`. That keeps the transcript in order and avoids a burst when signal returns.
- **Retries:** network errors, 408, 429 and 5xx retry indefinitely, with backoff capped at 30 seconds, and immediately on the browser's `online` event. Other 4xx answers keep the item and show Retry or Discard.
- **Survives reloads:** on load, uploading resumes for the open case.
- **Same interface:** the hook keeps its current one (`pending`, `enqueue`, `retry`, `waitForIdle`, `failedCount`, `reset`), so the EMT screen barely changes.

The backend is already ready for this. Re-sending a clip is harmless, and a late clip lands in the right place because the server corrects each clip's time with the device's send time (`routes/cases.py:97`).

**Things to get right.**
- **Numbering after a reload:** number new clips after the highest `seq` on the server *or* in the outbox. Otherwise two clips claim the same number and the server rejects one with a 409.
- **Ending the case:** the server refuses clips for a closed case (`routes/cases.py:917`). End case has to wait until the outbox is empty, or discard with a confirmation, as it does now.
- **Expired login:** uploads fail with 400 "Invalid token." Keep the items, ask the EMT to log in again, then resume.
- **Safari:** if storing a `Blob` in IndexedDB misbehaves, store an `ArrayBuffer` plus the MIME type. Clips are small: an 8-second clip is on the order of 100 KB.
- **Reports:** the end-of-report request goes through the outbox after its clips, so a report sent in a dead zone completes when signal returns. Typed updates and chat messages can use the same outbox later; they already carry client ids.

**Wake lock.** Hold `navigator.wakeLock.request('screen')` while recording, take it again on `visibilitychange` (it drops when the tab is hidden), and release it on stop. It keeps the screen from locking. It can't stop iOS suspending the page if the medic switches apps, so the recording screen should say to keep it open.

**Tests:** Jest with `fake-indexeddb` as a dev dependency.

**Demo moment:** turn on airplane mode in the middle of a report, keep talking, then turn it off. The hospital's transcript catches up in order and nothing is lost.

### Not now

- **Room assignment:** dropped for the hackathon.
- **Provisioning, sign-up rules and HIPAA:** out of scope. See [Before real patient data](#before-real-patient-data).

## Architecture changes

Documented here instead of done. These are the changes I'd make, sized for a hackathon.

### Principles

1. Change the code you're about to build in; leave stable code alone.
2. Clean-ups don't change behaviour. Behaviour changes get their own PR.
3. Move code rather than rewrite it. One PR per move, with tests unchanged except for import paths.
4. Skip anything that only pays off at scale.

### Target layout, after #32–#34

```
main.py                 app setup, startup recovery, mounts routers, wires the runners' on_change hooks
database.py             unchanged
openai_config.py        OpenAI client and model settings (#34)
thread_pools.py         shared thread pools (#34)
realtime.py             unchanged
case_state.py     new   the case as screens see it: CASE_SELECT, describe_cases, operational_status,
                        processing_summary, get_case; the one access rule
case_events.py    new   after a committed change: publish_case, publish_segment, record_queue
transcription.py  new   clip transcription, retries, error wording, startup recovery (reports go here later)
case_assessment.py      owns the one assessment-current rule; case_state imports it
case_extraction.py      unchanged
case_queue.py           unchanged
queue_history.py        unchanged
sas_sync.py             imports case_state instead of routes.cases
routes/cases.py         HTTP only: parse, authorize, call, publish (about half its size)
routes/hospitals.py new GET /api/hospitals, GET and PATCH /api/hospitals/mine
routes/messages.py      case threads use case_state's access rule
```

**Dependency rule:** routes import the domain modules, and domain modules never import routes. Today `sas_sync.py:195` imports `routes.cases`, and `case_assessment.py` and `case_extraction.py` import `routes.recordings` (#34 fixes those two).

`case_events.py` exists so that `transcription.py` can publish without importing a routes file. Keep the runners' `on_change` hooks, but wire them in `main.py`: importing `case_events` from `case_assessment` would be circular (`case_events` → `case_state` → `case_assessment`).

### Duplicates to remove

1. **"Is the AI assessment current?" is written five times:**
   - `routes/cases.py:163` (`processing_summary`) and `:259` (`describe_cases`)
   - `case_assessment.py:409` (the runner) and `:476` (startup, in SQL)
   - `demo/replay.py:515`

   Each compares the version, the acknowledged baseline and the ETA. Commit 09c5fee (ETA refreshes) had to add the ETA to all five. Fix: one `assessment_covers(attempt, case)` in `case_assessment.py`, which `case_state.py` imports. The startup query becomes a loop over open cases using it, and the replay can rely on the case's processing state from the API. Size S. Do it before reports.

2. **"Who can see this case?" is written four ways:**
   - `can_see_case` (`routes/cases.py:311`)
   - the stream filter `event_filter` (`:373`)
   - `authorize_live_case` in SQL (`routes/messages.py:98`)
   - the WHERE clauses in the list and queue routes (`routes/cases.py:503`, `:345`)

   It is the security rule (KAN-15), so it belongs in one place. Fix: one module returning both a check and the matching SQL condition. Size M. Behaviour-preserving, with the existing hospital-isolation tests as the guard.

3. **Two live channels per screen.**
   - Chat streams through its own endpoint, which re-reads the database every second (`routes/messages.py:22`).
   - The case view has `/api/cases/events`.
   - The client parses the two streams with two readers (`useCaseEvents.js` and `CaseChat.js`).

   Fix: publish `message.created` on the case stream, drop the per-thread stream for cases, and use one reader. Do it with the old-routes PR, because most of `tests/test_messages.py` tests the old recording threads. Not before the hackathon.

4. **The first single-recording flow.** It has its own prompt, transcription path and notifications, and it holds the known security holes listed in #34. Removal is the held KAN-15 PR. It can drop `twilio` and `sendgrid` from `requirements.txt` too.

5. **The Express-compatibility layer.** `main.py` returns validation errors and malformed JSON as 500, and a wrong method as an HTML 404, to match the removed Node server; `tests/test_parity.py` checks this. After #32 and the old-routes PR, decide whether to keep that contract. Switching to FastAPI's defaults (422, 405) is a behaviour change, so it gets its own PR. Low priority.

### Structural limits

These are fine for the hackathon; know where they are.

- **One process.** The event broker, the runners and the chat listeners live in memory (`realtime.py`). A second worker would split live events between processes and could run two assessments of the same case. Scaling means moving events to Redis or Postgres `LISTEN`/`NOTIFY` and the runners to a job queue.
- **SQLite on the event loop.**
  - `query()` and `run()` are synchronous inside async handlers.
  - Every published change re-sorts the hospital's whole queue (`publish_case` → `record_queue` → `describe_cases`, `routes/cases.py:339`).
  - Each open stream re-reads the user's role and hospital before every event it delivers (`routes/cases.py:393`).

  That's fine for a few hospitals and cases. If it gets slow, run database work in the thread pool first, then cache the user lookup per stream for a few seconds.
- **`python3 main.py` always runs uvicorn with `reload=True`** (`main.py:261`), including on the run sheet. Saving a `.py` file in the repo, or switching branches, restarts the server. Clips and assessments in flight are then marked "interrupted by a server restart". Make reload opt-in, for example with `RELOAD=1`. That's a small behaviour change, so it goes in its own PR.
- **Background work runs in-process,** as FastAPI background tasks and asyncio tasks. A crash loses it and startup recovers it, which is acceptable.

### Evaluation coupling

- The assessment uses the evaluation's prompt and contract. `tests/test_case_model.py:496` checks the contract against `evals/contracts.py`, but skips until Vrishank's branch is merged. Until then, nothing in `main` enforces that what the evaluation measures is what the app runs. Merging the harness, or vendoring `contracts.py`, turns the check on.
- Any change to `read_inputs` changes what the model sees. Reports and alerts are designed to leave the format alone. Reports do change *when* the model runs, to whole reports only, which brings the app closer to what the evaluation measures.

### Before real patient data

Out of scope now; this is the minimum list.
- Hospital accounts are self-service with any hospital (`routes/auth.py:50`).
- Logins last 24 hours in browser storage, with no revocation (`middleware/auth.py:12`).
- Audio in `private_uploads/` is unencrypted and kept forever.
- The old endpoints have the holes listed in #34.
- CORS allows any origin.
- There is no business associate agreement with the AI vendor.

## Done in this branch

- **Requirements.** `requirements.txt` pinned `httpx==0.27.2` and `test_requirements.txt` pinned `0.26.0`, so installing both failed. `test_requirements.txt` held only that pin and `pytest==7.4.3`, which `requirements.txt` already pins, and nothing referenced it, so it's deleted. A clean install of `requirements.txt` alone runs all nine test files: 308 passed, 2 skipped.
- **README.** Rewritten for the app as it is after #32:
  - The prompt is `awareness_v3`.
  - The test command covers all nine test files.
  - Removed: the steps that use `env.example` and `docker-compose`, neither of which exists; the "HIPAA Compliant" claim; and the old flow's risk scores and SMS/email features.
  - Added: a link to the run sheet and to this review.

## Open questions

- **Reports:** push-to-talk, or a microphone that stays on with Send marking each report?
- **Queue (clinician):** should acknowledgment reorder the board (already on the clinician's list)? Should crew alerts outrank the AI's categories?
- **Alerts in the AI input:** needs an evaluation run first (Vrishank).
- **Old routes (KAN-15):** remove, or lock the two open endpoints? (Ishaan and Anand.)
- **License:** the old README said MIT, but the repository has no `LICENSE` file.
- **`reload=True`:** make it opt-in?
