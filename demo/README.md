# Demo replay: three inbound patients

A repeatable run of the KAN-6 demo story through the real app. It does the same things an EMT and a doctor would: it creates cases, radios audio, sends updates and messages, and acknowledges new information. It talks only to the HTTP API, so it tests the real live-transcription, messaging, acknowledgment and failure-handling code.

**No real patient data.** The reports are fictional, AI-authored EMS reports from `docs/clinician-annotation/pilot_cases.json` (on `vrishank-branch` until it is merged). The update reports are copied verbatim; each initial report is the case's "earlier" information with its time phrase removed. `test_demo_replay.py` checks this against the pilot file once it is in the repo.

## Run it

```bash
SEED_DEMO_USERS=1 python3 main.py                        # in one terminal; OPENAI_API_KEY for real Whisper + scoring
python3 demo/make_audio.py                               # once; macOS `say`, offline (or --engine openai)
python3 demo/replay.py                                   # ~25 s; --seconds-per-minute 10 to present it live
```

Open the Doctor dashboard as `dr.smith` and an EMT dashboard as `emt.wilson`, `emt.garcia` or `emt.lee` (password `password123`) to watch.

For a clean screen (no closed cases from earlier runs in the doctor's list), run the server on its own database and delete it between rehearsals; your usual `asclepius.db` is untouched:

```bash
rm -f demo.db && ASCLEPIUS_DB_PATH=demo.db SEED_DEMO_USERS=1 python3 main.py
```

Export that run for SAS with the same setting: `ASCLEPIUS_DB_PATH=demo.db python -m sas_sync csv sas_export/`.

- `--typed` sends every report as a typed note: no audio files and no Whisper needed.
- `--finish` marks every patient arrived and closes the cases afterwards.
- Each run first closes whatever the scenario's EMTs left open, so it can be repeated.
- Without `OPENAI_API_KEY`, transcription and scoring fail. The dashboards show this as Needs Review with the reason, which is the intended behavior, but you won't see AI assessments.
- The summary ends with the run's model usage (assessments and extractions, tokens, list-price cost; transcription isn't included, about $0.0045 a minute of audio) and the **time to the doctor** in real seconds, whatever the replay speed: radio clip → transcript on screen, and new information → AI assessment and → extracted patient information. Both are also saved in `demo/runs/<run_id>.json`. A real-model run on Oct 11: 0.9 s, 4.0 s (including the 2 s wait for the rest of a report) and 5.0 s median.

## Presenting it (run sheet)

**Once:**
- Python packages in `.venv`: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. The system `python3` usually lacks them.
- Audio in `demo/audio/`: `python3 demo/make_audio.py`.
- A built client in `client/build/`, so one process serves everything. `npm ci` fails while `package-lock.json` is out of sync with `package.json`, so build in a copy and bring the build back:
  ```bash
  rm -rf /tmp/asclepius-client && cp -R client /tmp/asclepius-client && (cd /tmp/asclepius-client && npm install && npm run build) && rm -rf client/build && cp -R /tmp/asclepius-client/build client/build
  ```

**Each rehearsal**, from the repo root:

```bash
rm -f demo.db && ASCLEPIUS_DB_PATH=demo.db SEED_DEMO_USERS=1 SAS_SYNC_ENABLED=0 NODE_ENV=production PORT=5001 .venv/bin/python main.py
```

- **Port 5001:** on macOS, port 5000 belongs to AirPlay Receiver (Control Center).
- **`SAS_SYNC_ENABLED=0`:** keeps the SAS push quiet even if `.env` turns it on.
- **A fresh `demo.db`** keeps the doctor's list clean.

1. **Doctor:** open http://localhost:5001 and log in as `dr.smith`.
2. **EMT:** open a **private window or another browser** and log in as `emt.wilson`. Logins are per browser profile, so one window can't be both. The EMT screen picks up the case when the replay opens it.
3. **Run the story:**
   ```bash
   .venv/bin/python demo/replay.py --base-url http://localhost:5001 --seconds-per-minute 10
   ```
   It takes about 2½ minutes. Let the replay do the acknowledging. Opening cases to show them is fine.

| Real time | What to point at |
|---|---|
| 0:00–0:25 | Three crews open cases (Medic 8, 2, 10). Radio text reaches the doctor about 1 s after each clip. Vitals and findings fill in from the radio (hover a value for the medic's words). Cases go "Assessing…" → a category. The queue orders them, with "Why here". |
| 0:30 | The doctor acknowledges. Mike Wilson's screen shows "Seen by John Smith". |
| 0:40 | Messages between the doctor and Medic 10. |
| 1:10–1:20 | **Visible failure.** Medic 8's radio drops out: red "1 transcript segment failed", and Medic 8 jumps to the top (↑). The crew re-sends the update as text, and the AI moves Medic 8 to Prepare now (HR 118→128, BP 110/70→92/58, temp 39.1). The crew marks the lost clip handled, and it stays on record. |
| 1:30, 1:50 | Medic 2 and Medic 10 get worse on the radio. Rows show "↑ from #3" and what changed. Open one: the AI handoff compares with what the doctor acknowledged, and **NEW** tags mark exactly what arrived since. Medic 10, 11 min out, ends up first. |
| 2:00 | The doctor acknowledges everything. The EMT sees the toast. |
| end | The terminal prints the cost (about $0.002) and the time to the doctor (about 1 s for the transcript, 4 s for the assessment), and writes `demo/runs/<run_id>.json` and `<run_id>-predictions.json`. |

**If asked:**
- **A first report sometimes comes back "Cannot assess"** (fever without a temperature). The AI says what's missing instead of guessing. Once the doctor acknowledges it, it ranks below known Prepare-now patients but above Can wait (queue rule draft-2, still to be agreed with the clinician).
- **The queue order comes from a rule, not the model.** The model only labels each patient.

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
