"""Replay a demo scenario through the running app, as the EMTs and the doctor would.

    python3 demo/make_audio.py                        # once: render the reports to speech
    python3 demo/replay.py                            # against http://localhost:5000
    python3 demo/replay.py --typed                    # no audio/Whisper: reports go in as typed notes
    python3 demo/replay.py --seconds-per-minute 10    # slower, for presenting live

The server needs the demo accounts (SEED_DEMO_USERS=1) and, for real transcription and
scoring, OPENAI_API_KEY. Without the key, transcription and scoring fail visibly, which the
dashboards show as Needs Review.

Each run first closes any case the scenario's EMTs still have open, then creates fresh
cases, so the same scenario can be replayed any number of times. Cases store their
source_case_id (e.g. SYN002, the evaluation datasets' case_id) and source_run_id. The run
is also written to demo/runs/<run_id>.json with its speed, since scenario minutes are
compressed into seconds and analysis needs the scale.

Audio is uploaded the way the browser recorder does it: ~8 second segments with seq,
client_id and timestamps, so this exercises the real live-transcription path.
"""
import argparse
import array
import io
import json
import math
import statistics
import sys
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

DEMO_DIR = Path(__file__).parent
DEFAULT_SCENARIO = DEMO_DIR / "three_patients.json"
DEFAULT_AUDIO_DIR = DEMO_DIR / "audio"
DEFAULT_RUNS_DIR = DEMO_DIR / "runs"
# Same length as the browser recorder (SEGMENT_MS in LiveCaseRecorder.js).
SEGMENT_SECONDS = 8
# Under the server's 10 MiB per-segment limit (routes/cases.py MAX_SEGMENT_BYTES), with room for the header.
MAX_CLIP_BYTES = 9 * 1024 * 1024
# Longer than one segment's worst-case transcription (two 45 s attempts plus the retry delay).
TRANSCRIPT_WAIT_SECONDS = 120
# Password of the seeded demo accounts (database.insert_sample_data).
DEMO_PASSWORD = "password123"
ACTIONS = {"start_case", "audio_report", "typed_report", "garbled_audio", "dismiss_failed", "eta", "message",
           "acknowledge", "acknowledge_all", "finish_case", "snapshot_queue"}
# The app stores "Cannot assess" for the evaluation contract's "Unsure".
CATEGORY_FOR_EVALS = {"Cannot assess": "Unsure"}
FAILURE_STATUS = {"timed out": "timeout", "the AI declined to assess this case": "refusal"}


class ReplayError(Exception):
    pass


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _quietest_cut(samples, start: int, end: int, target: int, window: int) -> int:
    """Sample index in [start, end] at the quietest window, nearest the target on ties."""
    best, best_key = target, None
    for position in range(start, max(start + 1, end - window + 1), window):
        middle = position + window // 2
        key = (sum(abs(v) for v in samples[position:position + window]), abs(middle - target))
        if best_key is None or key < best_key:
            best, best_key = middle, key
    return best


def split_wav(data: bytes, seconds: float = SEGMENT_SECONDS) -> list:
    """
    Cut a WAV into consecutive clips of at most `seconds`, like the recorder's segments, but
    evenly sized and cut in pauses: Whisper mishears short fragments and words sliced in half
    (a 2-second tail turned "awake and" into "Awaken").
    """
    with wave.open(io.BytesIO(data)) as source:
        params = source.getparams()
        frames = source.readframes(source.getnframes())
    rate, width, channels = params.framerate, params.sampwidth, params.nchannels
    frame_bytes = width * channels
    total = len(frames) // frame_bytes
    limit = max(1, min(int(rate * seconds), MAX_CLIP_BYTES // frame_bytes))
    pieces = max(1, math.ceil(total / limit))
    samples = None
    if width == 2 and channels == 1:
        samples = array.array("h")
        samples.frombytes(frames[:total * 2])
        if sys.byteorder == "big":
            samples.byteswap()

    cuts, previous = [], 0
    for index in range(1, pieces):
        target = total * index // pieces
        # Search 1.5 s either side of the even split, without letting a clip exceed the limit.
        start = max(previous + 1, target - int(1.5 * rate), total - (pieces - index) * limit)
        end = min(previous + limit, target + int(1.5 * rate))
        cut = target if samples is None or end <= start else \
            _quietest_cut(samples, start, end, target, max(1, rate // 100))
        cuts.append(cut)
        previous = cut
    chunks = []
    for begin, finish in zip([0] + cuts, cuts + [total]):
        out = io.BytesIO()
        with wave.open(out, "wb") as chunk:
            chunk.setparams(params)
            chunk.writeframes(frames[begin * frame_bytes:finish * frame_bytes])
        chunks.append(out.getvalue())
    return chunks


def clip_duration_ms(data: bytes) -> int:
    """Length of a WAV clip; a full segment for anything unreadable (like the garbled clip)."""
    try:
        with wave.open(io.BytesIO(data)) as clip:
            return int(clip.getnframes() * 1000 / clip.getframerate())
    except (wave.Error, EOFError):
        return SEGMENT_SECONDS * 1000


def seconds_between(start: str, end: str) -> float:
    def parse(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parse(end) - parse(start)).total_seconds()


def spread(values: list):
    """Median and worst case in seconds, or None when nothing was measured."""
    if not values:
        return None
    return {"median_s": round(statistics.median(values), 1), "max_s": round(max(values), 1), "n": len(values)}


def validate(scenario: dict) -> None:
    patients = {p["source_case_id"] for p in scenario["patients"]}
    for event in scenario["events"]:
        if event["action"] not in ACTIONS:
            raise ReplayError(f"Unknown action {event['action']!r}")
        if event["action"] == "snapshot_queue":
            if not event.get("queue_id") or not event.get("patients") or set(event["patients"]) - patients:
                raise ReplayError(f"Queue snapshot at minute {event['minute']} needs a queue_id and known patients")
            continue
        if event["action"] != "acknowledge_all" and event.get("patient") not in patients:
            raise ReplayError(f"Event at minute {event['minute']} refers to unknown patient {event.get('patient')!r}")
        report = event.get("report")
        if report and report not in next(p for p in scenario["patients"] if p["source_case_id"] == event["patient"])["reports"]:
            raise ReplayError(f"{event['patient']} has no {report!r} report")


class Replay:
    def __init__(self, client: httpx.Client, scenario: dict, *, audio_dir: Path = DEFAULT_AUDIO_DIR, typed: bool = False,
                 seconds_per_minute: float = 2.0, sleep=time.sleep, log=print, password: str = DEMO_PASSWORD):
        validate(scenario)
        self.client = client
        self.scenario = scenario
        self.audio_dir = audio_dir
        self.typed = typed
        self.seconds_per_minute = seconds_per_minute
        self.sleep = sleep
        self.log = log
        self.password = password
        self.patients = {p["source_case_id"]: p for p in scenario["patients"]}
        self.run_id = uuid.uuid4().hex[:8]
        self.hospital_id = None
        self.tokens = {}
        self.cases = {}     # source_case_id -> case id
        self.next_seq = {}  # source_case_id -> next segment seq
        self.queue_snapshots = {}  # queue_id -> {source_case_id: rank within the group}

    # --- HTTP -----------------------------------------------------------------

    def _headers(self, username: str) -> dict:
        if username not in self.tokens:
            response = self.client.post("/api/auth/login", json={"username": username, "password": self.password})
            if response.status_code != 200:
                raise ReplayError(f"Login failed for {username} ({response.status_code}). Start the server with SEED_DEMO_USERS=1.")
            self.tokens[username] = response.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[username]}"}

    def call(self, method: str, path: str, username: str, **kwargs):
        response = self.client.request(method, path, headers=self._headers(username), **kwargs)
        if response.status_code >= 400:
            raise ReplayError(f"{method} {path} as {username} -> {response.status_code}: {response.text[:200]}")
        return response.json()

    # --- Scenario -------------------------------------------------------------

    def missing_audio(self) -> list:
        needed = {(e["patient"], e["report"]) for e in self.scenario["events"] if e["action"] == "audio_report"}
        return [str(self.audio_file(p, r)) for p, r in sorted(needed) if not self.audio_file(p, r).exists()]

    def audio_file(self, source_case_id: str, report: str) -> Path:
        return self.audio_dir / f"{source_case_id}-{report}.wav"

    def prepare(self) -> None:
        """Close cases the scenario's EMTs left open, so every run starts the same way."""
        for emt in sorted({p["emt"] for p in self.patients.values()}):
            for case in self.call("GET", "/api/cases?status=active", emt):
                self.call("POST", f"/api/cases/{case['id']}/close", emt)
                self.log(f"closed leftover case #{case['id']} for {emt}")
        hospitals = self.call("GET", "/api/hospitals", self.scenario["doctor"])
        matches = [h["id"] for h in hospitals if h["code"] == self.scenario["hospital_code"]]
        if not matches:
            raise ReplayError(f"No hospital with code {self.scenario['hospital_code']!r}")
        self.hospital_id = matches[0]

    def run(self) -> list:
        if not self.typed and self.missing_audio():
            raise ReplayError("Missing audio, run demo/make_audio.py first:\n  " + "\n  ".join(self.missing_audio()))
        self.prepare()
        self.started_at = iso(datetime.now(timezone.utc))
        started = time.monotonic()
        for event in sorted(self.scenario["events"], key=lambda e: e["minute"]):
            wait = started + event["minute"] * self.seconds_per_minute - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            getattr(self, event["action"])(event)
        return self.summary()

    def _emt(self, event) -> str:
        return self.patients[event["patient"]]["emt"]

    def _case(self, event) -> int:
        if event["patient"] not in self.cases:
            raise ReplayError(f"{event['action']} for {event['patient']} before its start_case event")
        return self.cases[event["patient"]]

    def start_case(self, event) -> None:
        patient = self.patients[event["patient"]]
        case = self.call("POST", "/api/cases", patient["emt"], json={
            "patient_info": patient["patient_info"],
            "destination_hospital_id": self.hospital_id,
            "ems_unit": patient["ems_unit"],
            "eta_minutes": patient["initial_eta_minutes"],
            "source_case_id": patient["source_case_id"],
            "source_run_id": self.run_id,
        })
        self.cases[event["patient"]] = case["id"]
        self.next_seq[event["patient"]] = 0
        self.log(f"[{event['minute']:>4}] {patient['emt']} started case #{case['id']} ({event['patient']})")

    def _upload(self, event, chunks: list, name: str) -> None:
        """Upload WAV clips as consecutive segments, timed as if the report had just been spoken."""
        durations = [clip_duration_ms(c) for c in chunks]
        now = datetime.now(timezone.utc)
        spoken_from = now - timedelta(milliseconds=sum(durations))
        for index, (chunk, duration) in enumerate(zip(chunks, durations)):
            seq = self.next_seq[event["patient"]]
            self.next_seq[event["patient"]] += 1
            self.call(
                "POST", f"/api/cases/{self._case(event)}/segments", self._emt(event),
                files={"audio": (f"{name}-{index}.wav", chunk, "audio/wav")},
                data={
                    "seq": str(seq),
                    "client_id": f"{self.run_id}-{event['patient']}-{seq}",
                    "recorded_at": iso(spoken_from + timedelta(milliseconds=sum(durations[:index]))),
                    "sent_at": iso(datetime.now(timezone.utc)),
                    "duration_ms": str(duration),
                },
            )

    def audio_report(self, event) -> None:
        if self.typed:
            return self.typed_report(event)
        chunks = split_wav(self.audio_file(event["patient"], event["report"]).read_bytes())
        self._upload(event, chunks, f"{event['patient']}-{event['report']}")
        self.log(f"[{event['minute']:>4}] {event['patient']}: {event['report']} report radioed ({len(chunks)} segment(s))")

    def typed_report(self, event) -> None:
        text = self.patients[event["patient"]]["reports"][event["report"]]
        self.call("POST", f"/api/cases/{self._case(event)}/updates", self._emt(event), json={
            "kind": "note", "body": text, "client_id": f"{self.run_id}-{event['patient']}-{event['report']}",
        })
        self.log(f"[{event['minute']:>4}] {event['patient']}: {event['report']} report typed")

    def garbled_audio(self, event) -> None:
        self._upload(event, [b"radio static " * 64], f"{event['patient']}-garbled")
        self.log(f"[{event['minute']:>4}] {event['patient']}: unreadable audio sent (transcription should fail visibly)")

    def dismiss_failed(self, event) -> None:
        """The crew marks failed clips as handled once their content was re-sent; the failure stays on record."""
        case_id = self._case(event)
        if not self._wait_for(lambda c: c["id"] != case_id or c["processing"]["transcription"]["pending"] == 0,
                              timeout=TRANSCRIPT_WAIT_SECONDS):
            raise ReplayError(f"{event['patient']} still transcribing after {TRANSCRIPT_WAIT_SECONDS} s; "
                              "can't tell which clips failed")
        for segment in self.call("GET", f"/api/cases/{case_id}/segments", self._emt(event)):
            if segment["status"] == "failed" and not segment["dismissed_at"]:
                self.call("POST", f"/api/cases/{case_id}/segments/{segment['id']}/dismiss", self._emt(event))
                self.log(f"[{event['minute']:>4}] {event['patient']}: crew dismissed failed clip {segment['seq']}")

    def eta(self, event) -> None:
        self.call("POST", f"/api/cases/{self._case(event)}/updates", self._emt(event),
                  json={"kind": "eta", "eta_minutes": event["minutes"]})
        self.log(f"[{event['minute']:>4}] {event['patient']}: ETA {event['minutes']} min")

    def message(self, event) -> None:
        sender = self.scenario["doctor"] if event["from"] == "doctor" else self._emt(event)
        self.call("POST", f"/api/cases/{self._case(event)}/messages", sender, json={"body": event["body"]})
        self.log(f"[{event['minute']:>4}] {event['patient']}: {event['from']} says {event['body']!r}")

    def acknowledge(self, event) -> None:
        """The doctor acknowledges one patient's information, once its transcript has arrived."""
        case_id, doctor = self._case(event), self.scenario["doctor"]
        if not self._wait_for(lambda c: c["id"] != case_id or c["processing"]["transcription"]["pending"] == 0,
                              timeout=TRANSCRIPT_WAIT_SECONDS):
            raise ReplayError(f"{event['patient']} still transcribing after {TRANSCRIPT_WAIT_SECONDS} s")
        version = self.call("GET", f"/api/cases/{case_id}", doctor)["info_version"]
        self.call("POST", f"/api/cases/{case_id}/acknowledge", doctor, json={"info_version": version})
        self.log(f"[{event['minute']:>4}] {doctor} acknowledged {event['patient']} at version {version}")

    def finish_case(self, event) -> None:
        """The patient arrives and the crew closes the case, so the EMT can start another."""
        case_id, emt = self._case(event), self._emt(event)
        self.call("POST", f"/api/cases/{case_id}/arrive", emt)
        self.call("POST", f"/api/cases/{case_id}/close", emt)
        self.log(f"[{event['minute']:>4}] {event['patient']}: arrived, case closed")

    def snapshot_queue(self, event) -> None:
        """Record how the hospital queue orders a group of patients, once their information is assessed."""
        group = {self.cases[p] for p in event["patients"] if p in self.cases}
        if len(group) != len(event["patients"]):
            raise ReplayError(f"Queue {event['queue_id']}: not every patient has a case yet")
        if not self._wait_for(lambda c: c["id"] not in group or self._settled(c), timeout=TRANSCRIPT_WAIT_SECONDS):
            raise ReplayError(f"Queue {event['queue_id']}: patients still being processed")
        positions = {c["source_case_id"]: c["queue_position"] for c in self.queue_order()["cases"]
                     if c["source_case_id"] in event["patients"]}
        if set(positions) != set(event["patients"]):
            raise ReplayError(f"Queue {event['queue_id']}: not every patient is in the hospital's queue")
        ranked = sorted(positions, key=positions.get)
        self.queue_snapshots[event["queue_id"]] = {p: ranked.index(p) + 1 for p in event["patients"]}
        self.log(f"[{event['minute']:>4}] queue {event['queue_id']}: " + " > ".join(ranked))

    def acknowledge_all(self, event) -> None:
        """
        The doctor acknowledges exactly the version on their screen, like the dashboard button,
        after reading any transcript still arriving (otherwise the ack covers an older version).
        """
        doctor = self.scenario["doctor"]
        if not self._wait_for(lambda c: c["processing"]["transcription"]["pending"] == 0,
                              timeout=TRANSCRIPT_WAIT_SECONDS):
            raise ReplayError(f"Transcripts still pending after {TRANSCRIPT_WAIT_SECONDS} s; "
                              "not acknowledging information the doctor hasn't seen")
        for source_case_id, case_id in self.cases.items():
            case = self.call("GET", f"/api/cases/{case_id}", doctor)
            self.call("POST", f"/api/cases/{case_id}/acknowledge", doctor, json={"info_version": case["info_version"]})
            self.log(f"[{event['minute']:>4}] {doctor} acknowledged {source_case_id} at version {case['info_version']}")

    # --- Results --------------------------------------------------------------

    def _wait_for(self, done, timeout: float) -> bool:
        """Poll the scenario's cases until done(case) holds for all of them, or the timeout passes."""
        deadline = time.monotonic() + timeout
        while True:
            cases = [self.call("GET", f"/api/cases/{case_id}", self.scenario["doctor"]) for case_id in self.cases.values()]
            if all(done(c) for c in cases):
                return True
            if time.monotonic() >= deadline:
                return False
            self.sleep(1)

    @staticmethod
    def _settled(case: dict) -> bool:
        return case["processing"]["transcription"]["pending"] == 0 \
            and case["processing"]["assessment"]["status"] not in ("processing", "pending")

    def wait_until_settled(self, timeout: float = 120.0) -> None:
        """Real transcription and scoring run after the requests return, including queued re-assessments."""
        settled = self._wait_for(self._settled, timeout)
        if not settled:
            self.log("still processing after the timeout; showing the current state")

    def manifest(self) -> dict:
        """What analysis needs to line this run up with the scenario: which cases, and the time scale."""
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario["scenario_id"],
            "dataset_version": self.scenario["dataset_version"],
            "started_at": self.started_at,
            "seconds_per_minute": self.seconds_per_minute,
            "typed": self.typed,
            "cases": self.cases,
        }

    def summary(self) -> list:
        return [self.call("GET", f"/api/cases/{case_id}", self.scenario["doctor"]) for case_id in self.cases.values()]

    def queue_order(self) -> dict:
        """The doctor's hospital queue as the server orders it (case_queue.py)."""
        return self.call("GET", "/api/cases/queue", self.scenario["doctor"])

    def model_usage(self) -> dict:
        """
        Tokens and estimated cost of every AI call in this run: assessments and patient information
        extractions. A call that never got an answer costs nothing; cost is None if an answered
        call's model is unpriced.
        """
        def attempts(kind):
            return [a for case_id in self.cases.values()
                    for a in self.call("GET", f"/api/cases/{case_id}/{kind}", self.scenario["doctor"])]
        assessments, extractions = attempts("assessments"), attempts("extractions")
        answered = [a for a in assessments + extractions if a["input_tokens"] is not None]
        return {
            "assessments": len(assessments),
            "extractions": len(extractions),
            "input_tokens": sum(a["input_tokens"] for a in answered),
            "output_tokens": sum(a["output_tokens"] or 0 for a in answered),
            "cost_usd": None if any(a["cost_usd"] is None for a in answered) else sum(a["cost_usd"] for a in answered),
        }

    def speed(self) -> dict:
        """
        Real seconds, whatever the replay speed, from information reaching the server to the
        doctor having it: a radio clip's transcript, and the AI assessment and patient information
        extraction that read the newest information. Re-assessments after an acknowledgment are
        left out: they answer "what changed since you looked", not "how fast".
        """
        doctor = self.scenario["doctor"]
        transcript, assessed, extracted = [], [], []
        for case_id in self.cases.values():
            arrived = {1: self.call("GET", f"/api/cases/{case_id}", doctor)["started_at"]}
            for segment in self.call("GET", f"/api/cases/{case_id}/segments", doctor):
                if segment["status"] == "completed" and segment["transcribed_at"]:
                    transcript.append(seconds_between(segment["created_at"], segment["transcribed_at"]))
                    if segment["info_version"]:
                        arrived[segment["info_version"]] = segment["created_at"]
            for update in self.call("GET", f"/api/cases/{case_id}/updates", doctor):
                if update["kind"] != "eta":  # an ETA change isn't new information to assess
                    arrived[update["info_version"]] = update["created_at"]
            first = {}
            for attempt in self.call("GET", f"/api/cases/{case_id}/assessments", doctor):
                version = attempt["based_on_version"]
                if attempt["status"] in ("completed", "needs_review") and version in arrived:
                    took = seconds_between(arrived[version], attempt["completed_at"])
                    first[version] = min(first.get(version, took), took)
            assessed += first.values()
            for attempt in self.call("GET", f"/api/cases/{case_id}/extractions", doctor):
                if attempt["status"] == "completed" and attempt["to_version"] in arrived:
                    extracted.append(seconds_between(arrived[attempt["to_version"]], attempt["completed_at"]))
        return {"transcript": spread(transcript), "assessment": spread(assessed), "extraction": spread(extracted)}

    def predictions(self) -> dict:
        """
        The run in the evaluation harness's model_predictions format (evals/README.md), so
        `python -m evals score` can compare what the app did with the clinician's answers:
        one record per patient from the app's last assessment of its final information, and a
        queue order for each snapshot. The inputs were the replayed reports as the app received
        them (input_contract_version), not the dataset's text verbatim; app_run says how.
        """
        doctor = self.scenario["doctor"]
        config_id = f"app-replay-{self.run_id}"
        records, details, versions = [], {}, set()
        for source_case_id, case_id in self.cases.items():
            case = self.call("GET", f"/api/cases/{case_id}", doctor)
            attempts = self.call("GET", f"/api/cases/{case_id}/assessments", doctor)  # newest first
            final = next((a for a in attempts if a["status"] != "processing"), None)
            record = {"config_id": config_id, "case_id": source_case_id, "repeat_id": 1, "retry_count": None}
            if final is None or final["based_on_version"] < case["info_version"]:
                record["status"] = "error"  # the final information was never assessed
            elif final["status"] in ("completed", "needs_review"):
                category = final["preparation_category"]
                record.update(status="ok", output={
                    "summary": final["summary"],
                    "meaningful_change": final["meaningful_change"],
                    "change_explanation": final["change_explanation"],
                    "missing_information": final["missing_information"],
                    "preparation_category": CATEGORY_FOR_EVALS.get(category, category),
                })
            else:
                record["status"] = FAILURE_STATUS.get(final["error"], "error")
            if final:
                versions.add(final["scorer_version"])
                record.update({key: final[key] for key in ("latency_ms", "input_tokens", "output_tokens", "cost_usd")})
            records.append(record)
            findings = self.call("GET", f"/api/cases/{case_id}/findings", doctor)
            facts = ([findings["chief_complaint"]] if findings["chief_complaint"] else []) \
                + findings["vitals"] + findings["findings"] + findings["treatments"]
            details[source_case_id] = {
                "case_id": case_id,
                "assessment_id": final and final["id"],
                "based_on_version": final and final["based_on_version"],
                "baseline_version": final and final["baseline_version"],
                # The extraction step's output (KAN-8), for reviewing alongside the assessment.
                "extracted": [{**{key: fact.get(key) for key in ("type", "text", "status", "name", "value", "unit",
                                                                   "qualifier")},
                               "source": fact["source"]["kind"]} for fact in facts],
            }
        if len(versions) > 1:
            raise ReplayError(f"Assessments in this run used different models/prompts: {sorted(versions)}")
        model, prompt_version = (versions.pop() if versions else "unknown/unknown").split("/", 1)
        return {
            "kind": "model_predictions",
            "dataset_version": self.scenario["dataset_version"],
            "configs": [{
                "config_id": config_id,
                "provider": "openai",
                "model": model,
                "prompt_version": prompt_version,
                "input_contract_version": f"app_replay_{'typed' if self.typed else 'speech'}",
                "repeats": 1,
            }],
            "records": records,
            "queues": [{"config_id": config_id, "queue_id": queue_id, "repeat_id": 1, "order": order}
                       for queue_id, order in self.queue_snapshots.items()],
            "app_run": {
                **self.manifest(),
                "notes": ("Outputs of the running app (Asclepius) on a replay. Each record is the app's last AI "
                          "assessment of the case's final information. Earlier information is what the doctor "
                          "had acknowledged; reports were " + ("typed" if self.typed else
                                                               "spoken (text-to-speech) and transcribed") +
                          ", and time was compressed by seconds_per_minute, which the model sees as "
                          "elapsed_minutes. Queue orders come from the app's queue rule, not a model."),
                "cases": details,
            },
        }

    def finish(self) -> None:
        """Hand-over: mark each patient arrived and close the case."""
        for source_case_id, case_id in self.cases.items():
            emt = self.patients[source_case_id]["emt"]
            self.call("POST", f"/api/cases/{case_id}/arrive", emt)
            self.call("POST", f"/api/cases/{case_id}/close", emt)


def print_summary(cases: list, usage: dict) -> None:
    print(f"\n{'source':<8} {'case':>5}  {'status':<13} {'preparation':<14} {'change':<8} {'processing':<13} acked  notes")
    for c in cases:
        assessment = c["current_assessment"] or {}
        print(f"{c['source_case_id'] or '-':<8} {c['id']:>5}  {c['operational_status']:<13} "
              f"{c['preparation_category'] or 'unavailable':<14} {assessment.get('meaningful_change') or '-':<8} "
              f"{c['processing']['status']:<13} {'yes' if c['latest_update_acknowledged'] else 'no':<6} "
              f"{'; '.join(c['processing']['reasons'])}")
    cost = "unknown" if usage["cost_usd"] is None else f"${usage['cost_usd']:.4f}"
    print(f"\nModel: {usage['assessments']} assessments + {usage['extractions']} extractions, "
          f"{usage['input_tokens']} input + {usage['output_tokens']} output tokens, {cost} "
          "(list price; transcription not included)")


def print_speed(speed: dict) -> None:
    labels = {"transcript": "radio clip -> transcript on screen", "assessment": "new information -> AI assessment",
              "extraction": "new information -> patient information"}
    print("\nTime to the doctor (real seconds, median / worst):")
    for key, label in labels.items():
        value = speed[key]
        print(f"  {label}: " + ("not measured" if value is None else
                                f"{value['median_s']} s / {value['max_s']} s (n={value['n']})"))


def print_queue(queue: dict) -> None:
    print(f"\nHospital queue ({queue['rule_version']}): {queue['rule']}")
    for case in queue["cases"]:
        print(f"  {case['queue_position']}. {case['source_case_id'] or case['id']}: {' · '.join(case['queue_reasons'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--typed", action="store_true", help="send reports as typed notes instead of audio")
    parser.add_argument("--seconds-per-minute", type=float, default=2.0, help="real seconds per scenario minute")
    parser.add_argument("--finish", action="store_true", help="afterwards, mark every patient arrived and close the cases")
    args = parser.parse_args()

    scenario = json.loads(args.scenario.read_text())
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        replay = Replay(client, scenario, audio_dir=args.audio_dir, typed=args.typed,
                        seconds_per_minute=args.seconds_per_minute)
        try:
            replay.run()
            replay.wait_until_settled()
            usage, speed = replay.model_usage(), replay.speed()
            DEFAULT_RUNS_DIR.mkdir(exist_ok=True)
            manifest_path = DEFAULT_RUNS_DIR / f"{replay.run_id}.json"
            manifest_path.write_text(json.dumps({**replay.manifest(), "model_usage": usage, "speed": speed},
                                                indent=2) + "\n")
            predictions_path = DEFAULT_RUNS_DIR / f"{replay.run_id}-predictions.json"
            predictions_path.write_text(json.dumps(replay.predictions(), indent=2) + "\n")
            print_summary(replay.summary(), usage)
            print_speed(speed)
            print_queue(replay.queue_order())
            print(f"\nRun {replay.run_id} saved to {manifest_path}")
            print(f"Predictions for the evaluation harness: {predictions_path}")
            if args.finish:
                replay.finish()
                print("\nAll patients marked arrived and cases closed.")
        except (ReplayError, httpx.HTTPError) as error:
            sys.exit(f"Replay failed: {error}")


if __name__ == "__main__":
    main()
