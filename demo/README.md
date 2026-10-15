# Demo replay: three inbound patients

A repeatable run of the KAN-6 demo story through the real app. It does the same things an EMT and a doctor would: it creates cases, radios audio, sends updates and messages, and acknowledges new information. It talks only to the HTTP API, so it tests the real live-transcription, messaging, acknowledgment and failure-handling code.

**No real patient data.** The reports are fictional, AI-authored EMS reports from `docs/clinician-annotation/pilot_cases.json`. The update reports are copied verbatim; each initial report is the case's "earlier" information with its time phrase removed. `tests/test_demo_replay.py` checks this against the included pilot fixture.

## Run it

```bash
SEED_DEMO_USERS=1 python3 main.py                        # in one terminal; OPENAI_API_KEY for real Whisper + scoring
python3 demo/make_audio.py                               # once; macOS `say`, offline (or --engine openai)
python3 demo/replay.py                                   # ~25 s; --seconds-per-minute 10 to present it live
```

Open the Doctor dashboard as `dr.smith` and an EMT dashboard as `emt.wilson`, `emt.garcia` or `emt.lee` (password `password123`) to watch.

For a clean screen (no closed cases from earlier runs in the doctor's list), give each rehearsal a new database and retain it with that run's evidence:

```bash
ASCLEPIUS_DB_PATH=demo-rehearsal-01.db SEED_DEMO_USERS=1 python3 main.py
```

Export that run for SAS with the same database setting: `ASCLEPIUS_DB_PATH=demo-rehearsal-01.db python -m backend.sas_sync csv sas_export/`.

- `--typed` sends every report as a typed note: no audio files and no Whisper needed.
- `--finish` marks every patient arrived and closes the cases afterwards.
- Each run first closes whatever the scenario's EMTs left open, so it can be repeated.
- Without `OPENAI_API_KEY`, transcription and scoring fail. The dashboards show this as Needs Review with the reason, which is the intended behavior, but you won't see AI assessments.
- The summary ends with:
  - **Model usage:** assessments and extractions, tokens, list-price cost, and calls that reported no usage.
  - **Transcription:** audio minutes sent, priced at the list rate per minute. This is an upper bound, since failed calls may not be billed.
  - **Time on the server,** in real seconds whatever the replay speed: clip received → transcript stored and pushed, and new information → AI assessment and → extracted patient information stored and pushed. These are server timestamps. They exclude the time spent recording a clip before upload, and the browser's receive and render time; neither is measured yet. A real-model run on Oct 11 gave 0.9 s, 4.0 s (including the 2 s wait for the rest of a report) and 5.0 s median.
- `demo/runs/<run_id>.json` saves all of this plus what the run used: the server's build from `GET /api/version` (code revision and whether it had local changes, client bundle hash, model and prompt versions, queue rule), the replay's own revision, and hashes of the scenario and audio.
- The replay waits for transcription, assessment **and** patient information extraction to finish before summarizing.

## Presenting it (run sheet)

**Once:**
- Python packages in `.venv`: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. The system `python3` usually lacks them.
- Audio in `demo/audio/`: `python3 demo/make_audio.py`.
- A built client in `client/build/`, so one process serves everything. The lockfile was fixed in PR #19; use the locked install:
  ```bash
  cd client
  npm ci
  CI=true npm test -- --watchAll=false --runInBand
  CI=true npm run build
  cd ..
  ```

**Each rehearsal**, from the repo root:

```bash
ASCLEPIUS_DB_PATH=demo-rehearsal-01.db SEED_DEMO_USERS=1 SAS_SYNC_ENABLED=0 NODE_ENV=production PORT=5001 .venv/bin/python main.py
```

- **Port 5001:** on macOS, port 5000 belongs to AirPlay Receiver (Control Center).
- **`SAS_SYNC_ENABLED=0`:** keeps the SAS push quiet even if `.env` turns it on.
- **A fresh database name per rehearsal** keeps the doctor's list clean and preserves previous run evidence.
- **Log in fresh before presenting:** the live feed ends when a login expires (after 24 hours).

1. **Doctor:** open http://localhost:5001 and log in as `dr.smith`.
2. **EMT:** open a **private window or another browser** and log in as `emt.lee` (Medic 8, the patient whose radio drops out and who gets the sepsis alert). Logins are per browser profile, so one window can't be both. The EMT screen picks up the case when the replay opens it.
   On the doctor's screen, press the **bell** (top right of the queue) if you want the chime for crew alerts.
3. **Run the story:**
   ```bash
   .venv/bin/python demo/replay.py --base-url http://localhost:5001 --seconds-per-minute 10
   ```
   It takes about 2½ minutes. Let the replay do the acknowledging. Opening cases to show them is fine.

| Real time | What to point at |
|---|---|
| 0:00–0:25 | Three crews open cases (Medic 8, 2, 10). Radio text reaches the doctor about 1 s after each clip. Vitals and findings fill in from the radio (hover a value for the medic's words). Cases go "Assessing…" → "AI suggests" a category. The queue orders them by a written rule (new information first, then crew alerts, then arrival time), never by the AI's label. Open a case: the "Why here" line under its title says why it sits where it does (hover a row for the same). |
| 0:30 | The doctor acknowledges. Jordan Lee's screen (Medic 8) shows "Seen by John Smith". |
| 0:40 | Messages between the doctor and Medic 10. |
| 1:10–1:20 | **Visible failure.** Medic 8's radio drops out: a red "Needs review · processing failed" badge and "1 transcript segment(s) failed to transcribe", and Medic 8 jumps to the top (↑ from #3). The crew re-sends the update as text, and the AI now suggests Prepare now (HR 118→128, BP 110/70→92/58, temp 39.1). The crew marks the lost clip handled, and it stays on record. Both screens show all of it. |
| 1:30, 1:50 | Medic 2 and Medic 10 get worse on the radio. Rows show "↑ from #3" and what changed. Open one: the AI handoff compares with what the doctor acknowledged, and **NEW** tags mark exactly what arrived since. Medic 10, 11 min out, ends up first. |
| 2:00 | The doctor acknowledges everything. The EMT sees the toast. |
| after the summary | **Crew alert.** On the EMT window, tap **Sepsis**, set the symptom onset or leave it blank ("not stated"), and tap **Call Sepsis alert**. Medic 8 jumps to #1 with "Crew alert not yet acknowledged". Open Medic 8 and tap **Acknowledge crew alert**: it stays at the top while the alert is active, and the EMT sees "Acknowledged by John Smith". Do this after the replay so its recorded queue stays clean for scoring. |
| then | **Export.** In Medic 8's case, **Download for the EHR (FHIR R4)**: a standard file a hospital record system can take in. Every AI-extracted fact carries the crew's exact words and the model version; the AI's category is left out. |
| end | The terminal prints the model cost (about $0.002), the transcription estimate, and the time on the server: about 1 s from a clip arriving to its transcript being pushed, about 4 s to the assessment. Say "on the server" if you quote it. It also writes `demo/runs/<run_id>.json` and `<run_id>-predictions.json`. |

**If asked:**
- **"When does the hospital see a report?"** Live draft words arrive during recording, in roughly eight-second batches plus transcription time. Send transcribes the complete recording again, so a number cut between draft clips is heard with its full context. Only the completed report updates the AI handoff and needs acknowledgment. The draft label matters: words can change, and transcription can still make mistakes. Crew alerts and typed updates remain immediate. The prepared replay uses clips cut at pauses; it does not measure microphone latency.
- **"What happens when the ETA changes?"** The AI re-assesses with the new ETA (prompt `awareness_v3`), and the doctor doesn't need to acknowledge again, because no new patient information came in. Until the new handoff is ready, the old one is marked "earlier info". The handoff shows when it was assessed; the live ETA is shown separately. Because the model reads the ETA, an ETA change alone can change the category; that's on the list for the clinician.
- **"How do you know the extracted facts are real?"** Each fact quotes the crew's own words, and the app checks that the quote really is in the clip or note it cites (hover a fact to see it). A bad quote fails that extraction visibly, with a retry, and earlier facts stay. It proves where a fact came from, not that the AI read it right.
- **Hospital setting (KAN-34):** "Settings" in the navbar, then "Arrival highlight", sets how early a patient is flagged "Arriving soon", per hospital: 1 to 60 minutes, default 5. It only changes the badge, never the AI or the order. To show it, set General to 15 as `dr.smith`; Northside (`dr.jones`) keeps its own value.
- **Filtering and sorting:** the tabs above the queue are filters, with the number in each: All, Not acknowledged (the new information nobody has acknowledged), Crew alerts, Needs review, AI: Prepare now and Arriving soon (a tab with nothing in it is hidden). Press one to show only those cases; press it again for everyone. The numbers always describe the whole queue. "Sort" offers Queue (the default), ETA, Updated and Waiting (longest without acknowledgment first). Both are only this screen's view: the hospital's order is unchanged, a reload resets them, and the Manual arrows are hidden while a filter or sort is on.
- **Custom risk fields:** "Settings" in the navbar opens "Custom risk fields": a doctor adds up to 10 yes/no, number or short-text fields (for example "On blood thinners"). They then show on every case under the vitals for the doctors to fill in, shared by all of that hospital's doctors. They never change the order.
- **A first report sometimes comes back "Cannot assess"** (fever without a temperature). The AI says what's missing instead of guessing, and the case is flagged for review until the doctor acknowledges it. Like every AI label, it never changes the order.
- **"Why did that patient move when the doctor acknowledged?"** Under the draft rule (draft-4), unacknowledged information comes first, so acknowledging a patient lets the others' new information go ahead of it. Every move is recorded with its cause, and the terminal prints each patient's path at the end. Whether acknowledgment should reorder the board at all is an open question for the clinician.
- **"Why doesn't the AI set the order?"** By default it doesn't, on purpose. FDA's January 2026 guidance on clinical decision support says software meant for time-critical decisions may not let the clinician independently check its basis, so it may count as a medical device. So **Standard**, the default, follows people's calls (crew alerts) and arrival time. The AI suggests a category and shows its sources, and the clinician decides. The rule (draft-4) still needs our clinician's agreement.
- **"Can the AI rank them?"** Yes, as an opt-in mode. The "Order" menu above the queue has three choices for the whole hospital: **Standard** (the written rule), **AI-assisted** and **Manual** (your team's own order, with arrows). AI-assisted is the same rule with the AI's category added after the people's calls: unacknowledged information and crew alerts still come first, then Prepare now, not yet known (including Cannot assess), Can wait, Routine, then arrival time. Where the AI moved a patient, hovering the row says "Standard order would put it #N". Each choice in the menu says what it does. The three-patient story ends with all three on Prepare now, so AI-assisted and Standard match there; to show a difference, switch to AI-assisted when cases have different categories. The queue history and the SAS export keep recording the Standard order in every mode.
- **"How is this different from Pulsara?"** For moving the case, the ETA and messages, it isn't; Pulsara does that well. The difference is what the hospital receives: a handoff it can check. It shows what changed since you last acknowledged, what's missing, and "Cannot assess" when there isn't enough, and ties every extracted fact to the crew's exact words, which the app checks. Pulsara's AI (June 2026) fills in fields from typed or dictated notes for the crew to confirm. Its route into the hospital record is a manual PDF upload or a custom API on a paid tier; ours is a standard FHIR file with the sources attached. *(Pulsara facts checked on its own pages, Oct 13.)*
- **"Does it work with Epic?"** It exports the handoff as FHIR R4, the standard Epic and other record systems use, and the file validates against the official FHIR models. We haven't connected it to a real Epic system yet, so don't claim integration.

## The story (`three_patients.json`)

The three patients are the clinician's **Queue exercise group Q02** (`SYN002`, `SYN008`, `SYN010`), all going to General Hospital. The order the app produces can therefore be compared with the clinician's ranking.

| Scenario minute | What happens |
|---|---|
| 0–2 | Three crews start cases and radio their first report |
| 3 | The doctor acknowledges all three |
| 4 | Doctor and the SYN010 crew exchange messages |
| 7–8 | SYN008's radio drops out: an unreadable clip fails transcription **visibly**, the crew re-sends the update as text, then marks the lost clip handled (it stays on record) |
| 9, 11 | SYN002 and SYN010 radio their deteriorating updates. The AI compares them with what the doctor acknowledged, and the cases need acknowledging again |
| 12 | The doctor acknowledges the new information; the EMTs see it |

Edit the `events` list to change the story; `replay.validate()` checks it.

Every case stores its `source_case_id` (the evaluation datasets' `case_id`) and `source_run_id`, so exported app data joins with the evaluation datasets (KAN-13, KAN-29, KAN-32). Each run also writes `demo/runs/<run_id>.json` with its case IDs and `seconds_per_minute`, because the replay compresses scenario minutes and analysis over time needs the scale.

## Scoring the app against the clinician (KAN-13)

Every replay also writes `demo/runs/<run_id>-predictions.json`: the run in the evaluation harness's `model_predictions` format (`evals/README.md`). It has:
- **One record per patient**, from the app's last AI assessment of the patient's final information. "Cannot assess" becomes the harness's "Unsure", and a failure becomes `error`, `timeout` or `refusal`.
- **A queue order** for each `snapshot_queue` event. The demo scenario records group Q02 at the end.
- **`app_run`**, describing how the inputs were made and each case's extracted patient information.

To score all ten pilot cases, generate a scenario from the dataset and replay it:

```bash
python3 demo/pilot_scenario.py --pilot docs/clinician-annotation/pilot_cases.json --queues evals/queues.json --out demo/pilot_cases_scenario.json
python3 demo/replay.py --scenario demo/pilot_cases_scenario.json --typed --seconds-per-minute 60
python -m evals score --references evals/local/approved-references.json --predictions demo/runs/<run_id>-predictions.json --out evals/results/app-replay-01
```

`pilot_scenario.py` replays each case the way the dataset describes it:
1. The earlier report is sent, and the doctor acknowledges it.
2. The current report arrives `elapsed_minutes` later, with the case's ETA.
3. Queue groups Q01 and Q02 each run together, and their order is recorded.

The run takes about 30 minutes at `--seconds-per-minute 60`. That speed is what makes the model see the dataset's `elapsed_minutes`; faster runs compress it (`app_run` records the speed). `--typed` sends the dataset's text. Without it, the reports are spoken (`python3 demo/make_audio.py --scenario demo/pilot_cases_scenario.json`) and transcribed, which measures the whole pipeline including speech.

The config's `input_contract_version` (`app_replay_typed` / `app_replay_speech`) marks that these are the app's inputs, not the dataset text sent straight to the model the way the harness's own runner does. Pilot cases are for development only.

## Who this serves

| Ticket | Use |
|---|---|
| KAN-6 Product delivery | The repeatable three-patient scenario |
| KAN-24 / KAN-25 Messaging, live transcription | End-to-end test with real speech through Whisper |
| KAN-8 Extraction | Transcripts whose facts are known (vitals are in the scripts), so the extracted vitals and findings can be checked |
| KAN-10 / KAN-31 Queue | Three cases at one hospital, plus the clinician's Q02 ranking to compare against |
| KAN-15 Security | `dr.jones` works at a different hospital (Northside), for "Hospital B can't see Hospital A" |
| KAN-29 / KAN-32 Export, SAS | Run the replay, then export |

## Limits

- The pilot cases are for **development only**. Don't tune prompts on replay results and then report held-out accuracy (see `evals/README.md`).
- These are text-to-speech voices. `--engine openai` sounds more natural for the video; both transcribe about equally well. For the jury video, teammates reading the same scripts (with some background noise) tests transcription on real speech.
- Reports are cut into roughly equal clips of at most 8 s, at pauses. Short fragments and words cut in half are what transcription gets wrong.
- The replay compresses time, so the `elapsed_minutes` the model sees is compressed too. Use `--seconds-per-minute 60` when a run must match the dataset's timing.
- Each case has one earlier report and one update, not a full transport.
