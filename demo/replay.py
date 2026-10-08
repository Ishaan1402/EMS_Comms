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
import io
import json
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
# Password of the seeded demo accounts (database.insert_sample_data).
DEMO_PASSWORD = "password123"
ACTIONS = {"start_case", "audio_report", "typed_report", "garbled_audio", "eta", "message", "acknowledge_all"}


class ReplayError(Exception):
    pass


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def split_wav(data: bytes, seconds: float = SEGMENT_SECONDS) -> list:
    """Cut a WAV into consecutive WAVs of at most `seconds`, like the recorder's segments."""
    chunks = []
    with wave.open(io.BytesIO(data)) as source:
        params = source.getparams()
        frames_per_chunk = int(source.getframerate() * seconds)
        while True:
            frames = source.readframes(frames_per_chunk)
            if not frames:
                break
            out = io.BytesIO()
            with wave.open(out, "wb") as chunk:
                chunk.setparams(params)
                chunk.writeframes(frames)
            chunks.append(out.getvalue())
    return chunks


def clip_duration_ms(data: bytes) -> int:
    """Length of a WAV clip; a full segment for anything unreadable (like the garbled clip)."""
    try:
        with wave.open(io.BytesIO(data)) as clip:
            return int(clip.getnframes() * 1000 / clip.getframerate())
    except (wave.Error, EOFError):
        return SEGMENT_SECONDS * 1000


def validate(scenario: dict) -> None:
    patients = {p["source_case_id"] for p in scenario["patients"]}
    for event in scenario["events"]:
        if event["action"] not in ACTIONS:
            raise ReplayError(f"Unknown action {event['action']!r}")
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

    def eta(self, event) -> None:
        self.call("POST", f"/api/cases/{self._case(event)}/updates", self._emt(event),
                  json={"kind": "eta", "eta_minutes": event["minutes"]})
        self.log(f"[{event['minute']:>4}] {event['patient']}: ETA {event['minutes']} min")

    def message(self, event) -> None:
        sender = self.scenario["doctor"] if event["from"] == "doctor" else self._emt(event)
        self.call("POST", f"/api/cases/{self._case(event)}/messages", sender, json={"body": event["body"]})
        self.log(f"[{event['minute']:>4}] {event['patient']}: {event['from']} says {event['body']!r}")

    def acknowledge_all(self, event) -> None:
        """The doctor acknowledges exactly the version on their screen, like the dashboard button."""
        doctor = self.scenario["doctor"]
        for source_case_id, case_id in self.cases.items():
            case = self.call("GET", f"/api/cases/{case_id}", doctor)
            self.call("POST", f"/api/cases/{case_id}/acknowledge", doctor, json={"info_version": case["info_version"]})
            self.log(f"[{event['minute']:>4}] {doctor} acknowledged {source_case_id} at version {case['info_version']}")

    # --- Results --------------------------------------------------------------

    def wait_until_settled(self, timeout: float = 90.0) -> None:
        """Real transcription and scoring run after the requests return; wait for them."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cases = [self.call("GET", f"/api/cases/{case_id}", self.scenario["doctor"]) for case_id in self.cases.values()]
            if all(c["processing"]["status"] != "processing" and c["processing"]["transcription"]["pending"] == 0
                   for c in cases):
                return
            self.sleep(1)
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

    def finish(self) -> None:
        """Hand-over: mark each patient arrived and close the case."""
        for source_case_id, case_id in self.cases.items():
            emt = self.patients[source_case_id]["emt"]
            self.call("POST", f"/api/cases/{case_id}/arrive", emt)
            self.call("POST", f"/api/cases/{case_id}/close", emt)


def print_summary(cases: list) -> None:
    print(f"\n{'source':<8} {'case':>5}  {'status':<13} {'risk':<10} {'processing':<13} acknowledged  notes")
    for c in cases:
        risk = f"{c['risk_score']}/10 P{c['priority_level']}" if c["risk_score"] is not None else "unavailable"
        print(f"{c['source_case_id'] or '-':<8} {c['id']:>5}  {c['operational_status']:<13} {risk:<10} "
              f"{c['processing']['status']:<13} {'yes' if c['latest_update_acknowledged'] else 'no':<13} "
              f"{'; '.join(c['processing']['reasons'])}")


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
            DEFAULT_RUNS_DIR.mkdir(exist_ok=True)
            manifest_path = DEFAULT_RUNS_DIR / f"{replay.run_id}.json"
            manifest_path.write_text(json.dumps(replay.manifest(), indent=2) + "\n")
            replay.wait_until_settled()
            print_summary(replay.summary())
            print(f"\nRun {replay.run_id} saved to {manifest_path}")
            if args.finish:
                replay.finish()
                print("\nAll patients marked arrived and cases closed.")
        except (ReplayError, httpx.HTTPError) as error:
            sys.exit(f"Replay failed: {error}")


if __name__ == "__main__":
    main()
