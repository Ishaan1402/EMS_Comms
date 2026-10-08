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

- `--typed` sends every report as a typed note: no audio files and no Whisper needed.
- `--finish` marks every patient arrived and closes the cases afterwards.
- Each run first closes whatever the scenario's EMTs left open, so it can be repeated.
- Without `OPENAI_API_KEY`, transcription and scoring fail. The dashboards show this as Needs Review with the reason, which is the intended behavior, but you won't see risk scores.

## The story (`three_patients.json`)

The three patients are the clinician's **Queue exercise group Q02** (`SYN002`, `SYN008`, `SYN010`), all going to General Hospital. The order the app produces can therefore be compared with the clinician's ranking.

| Scenario minute | What happens |
|---|---|
| 0–2 | Three crews start cases and radio their first report |
| 3 | The doctor acknowledges all three |
| 4 | Doctor and the SYN010 crew exchange messages |
| 7 | SYN008's radio drops out: an unreadable clip fails transcription **visibly**, and the crew re-sends the update as text |
| 9, 11 | SYN002 and SYN010 radio their deteriorating updates. They're re-assessed, and the cases need acknowledging again |
| 12 | The doctor acknowledges the new information; the EMTs see it |

Edit the `events` list to change the story; `replay.validate()` checks it.

Every case stores its `source_case_id` (the evaluation datasets' `case_id`) and `source_run_id`, so exported app data joins with the evaluation datasets (KAN-13, KAN-29, KAN-32). Each run also writes `demo/runs/<run_id>.json` with its case IDs and `seconds_per_minute`, because the replay compresses scenario minutes and analysis over time needs the scale.

## Who this serves

| Ticket | Use |
|---|---|
| KAN-6 Product delivery | The repeatable three-patient scenario |
| KAN-24 / KAN-25 Messaging, live transcription | End-to-end test with real speech through Whisper |
| KAN-8 Extraction | Transcripts whose facts are known (vitals are in the scripts) |
| KAN-10 / KAN-31 Queue | Three cases at one hospital, plus the clinician's Q02 ranking to compare against |
| KAN-15 Security | `dr.jones` works at a different hospital (Northside), for "Hospital B can't see Hospital A" |
| KAN-29 / KAN-32 Export, SAS | Run the replay, then export |

## Limits

- The pilot cases are for **development only**. Don't tune prompts on replay results and then report held-out accuracy (see `evals/README.md`).
- These are text-to-speech voices. For the jury video, teammates reading the same scripts (with some background noise) tests Whisper on real speech.
- Each case has one earlier report and one update, not a full transport.
