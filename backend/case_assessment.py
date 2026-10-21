"""AI assessment of EMS cases, on the evaluation contract (evals/contracts.py).

The model gets a versioned system prompt (evals/prompts/awareness_v4.txt) and the same input
and output fields as the evaluation harness. Compare runs using the recorded prompt version;
v4 keeps v3's wording and replaces its preparation category with a suggested ESI level and a
risk score. Output fields:
    summary, meaningful_change, change_explanation, missing_information,
    esi_level, esi_reason, risk_score, risk_reason
The category the app showed before v4 is still stored, derived from the ESI level
(PREPARATION_FOR_ESI), so evaluations, queue history and SAS exports keep their column.

"Earlier" information is what the hospital had acknowledged before this assessment, so
meaningful_change answers "what changed since the hospital last looked". Before any
acknowledgment everything is current, as in a first report.

Every attempt is stored in risk_assessments with the case info_version it read, so:
- a failure never overwrites earlier information or the last usable assessment;
- a slow result for older information can't replace a newer one (the current
  assessment is the usable one with the highest based_on_version);
- unusable output is marked needs_review with a reason, never replaced by a default.

Runs in-process (like realtime.py), so use a single server worker.
"""
import asyncio
import concurrent.futures
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import openai

from backend import case_reports
from backend.database import get_db, query, run, utc_now_iso
from backend import openai_config

PROMPT_VERSION = "awareness_v4"
PROMPT = (Path(__file__).parent.parent / "evals" / "prompts" / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")

# The evaluation contract, kept identical to evals/contracts.py (test_case_model checks this).
CHANGE = ("Yes", "No", "Unclear", "No earlier report")
PREPARATION = ("Prepare now", "Can wait", "Routine", "Unsure")
ESI = ("1", "2", "3", "4", "5", "Cannot assess")
RISK_SCORES = range(1, 11)
OUTPUT_FIELDS = ("summary", "meaningful_change", "change_explanation", "missing_information",
                 "esi_level", "esi_reason", "risk_score", "risk_reason")
TEXT_FIELDS = ("summary", "change_explanation", "missing_information", "esi_reason", "risk_reason")
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        **{field: {"type": "string"} for field in OUTPUT_FIELDS},
        "meaningful_change": {"type": "string", "enum": list(CHANGE)},
        "esi_level": {"type": "string", "enum": list(ESI)},
        "risk_score": {"type": "integer"},  # 1 to 10, checked by output_errors
    },
    "required": list(OUTPUT_FIELDS),
    "additionalProperties": False,
}
# Wording of pilot_cases.json for a case with no earlier report.
NO_EARLIER_REPORT = "No earlier report provided."

# The prompt's "Unsure" is the older wording; the app stores and shows "Cannot assess".
CANNOT_ASSESS = "Cannot assess"
CANNOT_ASSESS_REASON = "Insufficient information to suggest an ESI level."
# The project's own mapping from ESI to the earlier category (evals/contracts.PREPARATION_FOR_ESI uses
# "Unsure" for Cannot assess). Not clinically validated.
PREPARATION_FOR_ESI = {1: "Prepare now", 2: "Prepare now", 3: "Can wait", 4: "Routine", 5: "Routine"}
UNREADABLE_ERROR = "the AI's answer didn't follow the expected format"

# List prices in USD per million tokens (input, output), checked 2026-10-07. A model missing
# here gets no cost estimate rather than a wrong one.
PRICES_PER_MILLION = {"gpt-6-luna": (0.10, 0.50)}

# Longer than the request timeout times its attempts (openai_config.SCORING_REQUEST_TIMEOUT_SECONDS,
# OPENAI_MAX_RETRIES), so the worker thread is free again by the time a call is given up on.
SCORING_TIMEOUT_SECONDS = 90
# Own pool, so assessments never queue behind the older recording flow's calls.
assessment_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="assess")
# Wait this long before assessing, and again before re-assessing information that arrived
# during a run, so a report that arrives as several segments is assessed once, whole.
SETTLE_SECONDS = 2.0
# Then wait for segments still being transcribed: assessing without them would be out of date
# as soon as they finish. Bounded, so a slow transcription can't hold the assessment back for
# long (its text is assessed when it arrives).
TRANSCRIPTION_WAIT_SECONDS = 60
TRANSCRIPTION_POLL_SECONDS = 0.25

ASSESSMENT_COLUMNS = (
    "id, case_id, based_on_version, baseline_version, input_eta_at, status, preparation_category, meaningful_change, "
    "esi_level, esi_reason, risk_score, risk_reason, summary, change_explanation, missing_information, review_reason, error, scorer_version, "
    "input_tokens, output_tokens, latency_ms, cost_usd, started_at, completed_at, updated_at"
)


def scorer_version() -> str:
    """Model and prompt that produced an assessment, stored with it."""
    return f"{openai_config.SCORING_MODEL}/{PROMPT_VERSION}"


# Vital signs an EMT can enter: name -> (label, unit, min, max).
VITAL_SIGNS = {
    "hr": ("HR", "bpm", 0, 300),
    "sbp": ("Systolic BP", "mmHg", 0, 300),
    "dbp": ("Diastolic BP", "mmHg", 0, 250),
    "rr": ("RR", "/min", 0, 100),
    "spo2": ("SpO2", "%", 0, 100),
    "temp_c": ("Temp", "°C", 20, 45),
    "gcs": ("GCS", "", 3, 15),
    "glucose": ("Glucose", "mg/dL", 0, 2000),
}


def format_vitals(readings: list) -> str:
    """e.g. "HR 110 bpm, SpO2 91 %" in VITAL_SIGNS order."""
    values = {r["name"]: r["value"] for r in readings}
    parts = []
    for name, (label, unit, _, _) in VITAL_SIGNS.items():
        if name in values:
            value = values[name]
            number = int(value) if float(value).is_integer() else value
            parts.append(f"{label} {number} {unit}".strip())
    return ", ".join(parts)


class ScoringUnavailable(Exception):
    pass


class ScoringRefused(Exception):
    pass


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format(items: list) -> str:
    """Radio transcript segments run together as speech (they're cut mid-sentence); typed entries get a line each."""
    paragraphs, speech = [], []
    for _, _, _, text, typed in items:
        if typed:
            if speech:
                paragraphs.append(" ".join(speech))
                speech = []
            paragraphs.append(text)
        else:
            speech.append(text)
    if speech:
        paragraphs.append(" ".join(speech))
    return "\n".join(paragraphs)


def read_inputs(case_id: int) -> Optional[dict]:
    """
    The model input for the case's current information, in the evaluation harness's fields.
    Read in one transaction so it matches info_version exactly.
    """
    with get_db() as conn:
        conn.execute("BEGIN")
        case = conn.execute(
            "SELECT id, patient_info, info_version, started_at, eta_at, source_case_id FROM cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if case is None:
            return None
        version = case["info_version"]
        baseline = conn.execute(
            "SELECT MAX(info_version) FROM case_acknowledgments WHERE case_id = ? AND info_version < ?",
            (case_id, version),
        ).fetchone()[0]
        segments = conn.execute(
            f"""SELECT seq, text, info_version, recorded_at FROM transcript_segments
                WHERE case_id = ? AND status = 'completed' AND text != '' AND {case_reports.FINISHED_CLIP}
                ORDER BY seq""",
            (case_id,),
        ).fetchall()
        updates = conn.execute(
            """SELECT id, info_version, kind, body, created_at FROM case_updates
               WHERE case_id = ? AND kind != 'eta' ORDER BY id""",
            (case_id,),
        ).fetchall()
        vitals = conn.execute(
            "SELECT update_id, name, value FROM vital_readings WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()

    vitals_by_update = {}
    for reading in vitals:
        vitals_by_update.setdefault(reading["update_id"], []).append(dict(reading))

    # (version, time, sort key, text, typed) for every piece of information. Ordered by when it was
    # spoken or typed: segments finish transcribing out of order, so their versions are not speech order.
    # The summary typed when the case was opened always comes first.
    # A version of None (text transcribed before versions were recorded) is treated as not yet seen.
    items = []
    if case["patient_info"]:
        items.append((1, case["started_at"], ("", -1), f"Typed by the crew: {case['patient_info']}", True))
    for update in updates:
        label = {"note": "Typed by the crew", "vitals": "Vitals typed by the crew",
                 "correction": "Correction typed by the crew (replaces earlier information)"}[update["kind"]]
        parts = []
        if update["id"] in vitals_by_update:
            parts.append(format_vitals(vitals_by_update[update["id"]]))
        if update["body"]:
            parts.append(update["body"])
        items.append((update["info_version"], update["created_at"], (update["created_at"], update["id"]),
                      f"{label}: {' — '.join(parts)}", True))
    for segment in segments:
        items.append((segment["info_version"], segment["recorded_at"], (segment["recorded_at"], segment["seq"]),
                      segment["text"], False))
    items.sort(key=lambda item: item[2])

    def acknowledged(item):
        return baseline is not None and item[0] is not None and item[0] <= baseline

    prior = [item for item in items if acknowledged(item)]
    current = [item for item in items if not acknowledged(item)]
    if not current:  # nothing newer than what was acknowledged: assess it all as current
        prior, current = [], items

    now = datetime.now(timezone.utc)
    elapsed = 0
    if prior and current and prior[-1][1] and current[-1][1]:
        elapsed = max(0, round((_parse_time(current[-1][1]) - _parse_time(prior[-1][1])).total_seconds() / 60))
    eta = max(0, round((_parse_time(case["eta_at"]) - now).total_seconds() / 60)) if case["eta_at"] else None
    label = case["source_case_id"] or f"case-{case_id}"
    return {
        "case_id": case_id,
        "version": version,
        "baseline_version": baseline,
        "eta_at": case["eta_at"],
        "has_information": bool(items),
        "input": {
            "case_id": label,
            "encounter_id": label,
            "update_number": 2 if prior else 1,
            "elapsed_minutes": elapsed,
            "eta_minutes": eta,
            "prior_information": _format(prior) or NO_EARLIER_REPORT,
            "current_transcript": _format(current),
        },
    }


async def score(inputs: dict):
    """
    Call the model with the evaluation prompt and schema. Returns (parsed output or None if
    unreadable, usage). Raises on any failure; never fills in defaults.
    """
    client = openai_config.openai_client
    if not client:
        raise ScoringUnavailable()
    started = time.monotonic()
    response = await asyncio.get_running_loop().run_in_executor(
        assessment_executor,
        lambda: client.chat.completions.create(
            model=openai_config.SCORING_MODEL,
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(inputs["input"], ensure_ascii=False)},
            ],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "ems_output", "strict": True, "schema": OUTPUT_SCHEMA}},
            extra_body=openai_config.scoring_extra_body(),
            timeout=openai_config.SCORING_REQUEST_TIMEOUT_SECONDS,
        ),
    )
    usage = {
        "input_tokens": getattr(response.usage, "prompt_tokens", None),
        "output_tokens": getattr(response.usage, "completion_tokens", None),
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise ScoringRefused()
    try:
        return json.loads(message.content or ""), usage
    except ValueError:
        return None, usage


def output_errors(output) -> list:
    """Same checks as evals/contracts.output_errors."""
    if not isinstance(output, dict):
        return ["output must be an object"]
    errors = []
    if set(output) != set(OUTPUT_FIELDS):
        errors.append("output fields do not match the contract")
    for key in TEXT_FIELDS:
        if not isinstance(output.get(key), str) or not output[key].strip():
            errors.append(f"missing/invalid {key}")
    if output.get("meaningful_change") not in CHANGE:
        errors.append("invalid meaningful_change")
    if output.get("esi_level") not in ESI:
        errors.append("invalid esi_level")
    risk = output.get("risk_score")
    if isinstance(risk, bool) or risk not in RISK_SCORES:
        errors.append("invalid risk_score")
    return errors


def interpret(output) -> dict:
    """
    Turn model output into the stored fields. An unreadable answer is a processing failure
    (retryable, and the last usable assessment stays current), not a clinical Needs Review.
    """
    if output_errors(output):
        return {"status": "failed", "error": UNREADABLE_ERROR}
    fields = {field: output[field].strip() for field in TEXT_FIELDS}
    fields["meaningful_change"] = output["meaningful_change"]
    fields["risk_score"] = output["risk_score"]
    if output["esi_level"] == CANNOT_ASSESS:
        fields.update(esi_level=None, preparation_category=CANNOT_ASSESS)
        return {**fields, "status": "needs_review", "review_reason": CANNOT_ASSESS_REASON}
    fields["esi_level"] = int(output["esi_level"])
    fields["preparation_category"] = PREPARATION_FOR_ESI[fields["esi_level"]]
    # The risk score is only shown and breaks ties in AI-assisted, so it never flags a case for review.
    return {**fields, "status": "completed", "review_reason": None}


# A number written in digits, not part of a name such as SpO2; "120/80" is two numbers, "1,000" one.
DIGITS = re.compile(r"(?<![A-Za-z\d.,])\d{1,3}(?:,\d{3})+(?![\d,])|(?<![A-Za-z\d.])\d+(?:\.\d+)?")
# A handoff number that is a temperature, so it may be the reports' value converted between °F and °C.
DEGREES_AFTER = re.compile(r"\s*(?:°|º|degrees?\b|deg\b|[CF]\b)", re.IGNORECASE)
TEMPERATURE_BEFORE = re.compile(r"\b(?:temp\w*|febrile|fever)\W+(?:\w+\W+)?$", re.IGNORECASE)
# The scale after a GCS score written as "14/15".
GCS_OUT_OF = re.compile(r"(?<=\d)/15\b")
NUMBER_WORDS = {word: n for n, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
NUMBER_WORDS.update({word: 10 * n for n, word in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)})
NUMBER_WORDS.update(a=1, an=1, oh=0)


def spoken_numbers(text: str) -> set:
    """
    Numbers said in words, as radio reports say them: "ninety two" (92), "thirty-nine point one"
    (39.1), "one hundred and ten" (110), "one forty" (140) and "one oh five" (105). Each word's own
    value counts too, so an ambiguous phrase allows every reading.
    """
    found = set()
    numbers, hundred, point = [], False, False
    for word in re.findall(r"[a-z]+", text.lower()) + [""]:
        if word in NUMBER_WORDS:
            n = NUMBER_WORDS[word]
            found.add(n)
            if point and numbers and n < 10:
                numbers[-1] = round(numbers[-1] + n / 10, 1)
            elif numbers and n < 10 and numbers[-1] % 10 == 0 and numbers[-1] % 100 >= 20:
                numbers[-1] += n
            elif hundred and numbers and n < 100 and numbers[-1] % 100 == 0:
                numbers[-1] += n
            else:
                numbers.append(n)
            found.add(numbers[-1])
            point = False
        elif word == "hundred":
            numbers[-1:] = [(numbers[-1] if numbers else 1) * 100]
            hundred = True
        elif word == "point" and numbers:
            point = True
        elif word == "and" and hundred:
            continue
        else:
            found.update(numbers)
            found.update(a * 100 + b for a, b in zip(numbers, numbers[1:]) if a < 10 and 10 <= b < 100)
            found.update(a * 100 + b for a, zero, b in zip(numbers, numbers[1:], numbers[2:])
                         if 0 < a < 10 and zero == 0 and b < 10)
            numbers, hundred, point = [], False, False
    return found


def numbers_in(text: str) -> set:
    """Every number the text states, in digits or in words. A time such as 1430 also states 14 and 30."""
    numbers = {float(n.replace(",", "")) for n in DIGITS.findall(text)} | {float(n) for n in spoken_numbers(text)}
    return numbers | {part for n in numbers if n.is_integer() and 100 <= n <= 2359 for part in divmod(n, 100)}


def stated(number: float, numbers: set, temperature: bool = False) -> bool:
    """Whether a number is one of these, as given or (for a temperature) converted between °F and °C."""
    return any(number == n or temperature and (abs(number - (n - 32) * 5 / 9) <= 0.5
                                               or abs(number - (n * 9 / 5 + 32)) <= 0.5)
               for n in numbers)


# The ESI reasons may also cite the ESI scale itself: a level, a count of resources, a score out of 10,
# or a danger-zone threshold (the prompt's decision points), none of which the crew states.
ESI_REFERENCE = re.compile(r"\bESI\s*[1-5]\b|\b(?:[1-9]|10)\s*/\s*10\b", re.IGNORECASE)
ESI_THRESHOLDS = {1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 20.0, 30.0, 35.0, 38.0, 40.0, 55.0, 60.0, 92.0, 100.0, 120.0, 140.0,
                  180.0, 190.0}


def unsupported_numbers(fields: dict, inputs: dict) -> list:
    """
    Numbers in the handoff and the ESI and risk reasons that nothing the model read states: not the
    reports, typed vitals, ETA or elapsed time. A wrong vital in a handoff looks as trustworthy as a right one.
    """
    given = inputs["input"]
    numbers = numbers_in(f"{given['prior_information']}\n{given['current_transcript']}")
    numbers |= {float(given[key]) for key in ("eta_minutes", "elapsed_minutes") if given[key] is not None}
    unstated = _unstated(f"{fields['summary']}\n{fields['change_explanation']}", numbers)
    reasons = ESI_REFERENCE.sub("", f"{fields.get('esi_reason') or ''}\n{fields.get('risk_reason') or ''}")
    unstated += _unstated(reasons, numbers | ESI_THRESHOLDS)
    return list(dict.fromkeys(unstated))


def _unstated(text: str, numbers: set) -> list:
    text = GCS_OUT_OF.sub("", text)
    unstated = []
    for match in DIGITS.finditer(text):
        is_temperature = bool(DEGREES_AFTER.match(text, match.end()) or TEMPERATURE_BEFORE.search(text[:match.start()]))
        if not stated(float(match.group().replace(",", "")), numbers, temperature=is_temperature):
            unstated.append(match.group())
    return unstated


def check_numbers(fields: dict, inputs: dict) -> dict:
    """A handoff with a number the reports don't state needs review, with the numbers named."""
    if fields["status"] == "failed":
        return fields
    missing = unsupported_numbers(fields, inputs)
    if not missing:
        return fields
    reason = f"Handoff mentions {', '.join(missing)}, not found in the crew's reports. Check before relying on it."
    return {**fields, "status": "needs_review",
            "review_reason": f"{fields['review_reason']} {reason}" if fields["review_reason"] else reason}


def estimate_cost(model: str, usage: dict) -> Optional[float]:
    prices = PRICES_PER_MILLION.get(model)
    if not prices or usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return None
    return (usage["input_tokens"] * prices[0] + usage["output_tokens"] * prices[1]) / 1_000_000


def describe_error(error: BaseException) -> str:
    """Short reason shown to clinicians after "Assessment failed: "."""
    if isinstance(error, ScoringUnavailable):
        return "AI scoring is not configured"
    if isinstance(error, ScoringRefused):
        return "the AI declined to assess this case"
    if isinstance(error, (asyncio.TimeoutError, openai.APITimeoutError)):
        return "timed out"
    if isinstance(error, openai.APIConnectionError):
        return "could not reach the AI service"
    if isinstance(error, openai.RateLimitError):
        return "AI service is busy (rate limited)"
    if isinstance(error, openai.APIError):
        return "AI service error"
    return "unexpected error"


def get_assessment(assessment_id: int) -> Optional[dict]:
    rows = query(f"SELECT {ASSESSMENT_COLUMNS} FROM risk_assessments WHERE id = ?", (assessment_id,))
    return rows[0] if rows else None


def transcription_in_flight(case_id: int) -> bool:
    return bool(query("SELECT 1 FROM transcript_segments WHERE case_id = ? AND is_preview = 0 AND status = 'pending' LIMIT 1",
                      (case_id,)))


def covers(attempt: dict, info_version: int, baseline_version: Optional[int], eta_at: Optional[str]) -> bool:
    """
    Whether an attempt is current: it read this information, compared against this acknowledged
    baseline, with this expected arrival. The one definition used by the runner, startup recovery,
    the case API and the replay (via the API). An older attempt with no recorded ETA ('') is never current.
    """
    return (attempt["based_on_version"] >= info_version and attempt["baseline_version"] == baseline_version
            and attempt["input_eta_at"] == eta_at)


def latest_attempt(case_id: int) -> Optional[dict]:
    rows = query(
        f"SELECT {ASSESSMENT_COLUMNS} FROM risk_assessments WHERE case_id = ? ORDER BY id DESC LIMIT 1",
        (case_id,),
    )
    return rows[0] if rows else None


class CaseRunner:
    """
    At most one run per case at a time; information that arrives meanwhile is processed next.
    Subclasses do the work in _run(case_id, force): AssessmentRunner here, and the extraction
    step's runner in case_extraction.py, so each pipeline step queues and fails on its own.
    """

    def __init__(self):
        self._active = set()
        self._queued = {}  # case_id -> force
        self._tasks = set()  # started by start(); referenced so they aren't garbage-collected
        # Called with the case id after every stored change; routes/cases.py publishes it.
        self.on_change: Callable[[int], None] = lambda case_id: None

    async def request(self, case_id: int, force: bool = False) -> None:
        """
        Process the case's current information unless a run already covers it.
        force re-runs even if the latest run (a failure) covers the current version.
        """
        if case_id in self._active:
            self._queued[case_id] = self._queued.get(case_id, False) or force
            return
        self._active.add(case_id)
        try:
            await self._settle(case_id)
            # Anything that arrived while settling is read now; keep a retry's force flag.
            force = self._queued.pop(case_id, False) or force
            await self._run(case_id, force)
            while case_id in self._queued:
                force = self._queued.pop(case_id) or force
                await self._settle(case_id)
                await self._run(case_id, force)
        finally:
            self._active.discard(case_id)
            self._queued.pop(case_id, None)

    @staticmethod
    async def _settle(case_id: int) -> None:
        """Let the rest of a report arrive: a short pause, then any transcription still in flight."""
        await asyncio.sleep(SETTLE_SECONDS)
        deadline = time.monotonic() + TRANSCRIPTION_WAIT_SECONDS
        while transcription_in_flight(case_id) and time.monotonic() < deadline:
            await asyncio.sleep(TRANSCRIPTION_POLL_SECONDS)

    def start(self, case_id: int, force: bool = False) -> None:
        """request() as a background task, for callers that aren't awaiting it (startup)."""
        task = asyncio.get_running_loop().create_task(self.request(case_id, force))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, case_id: int, force: bool) -> None:
        raise NotImplementedError


class AssessmentRunner(CaseRunner):
    """At most one assessment per case at a time; information that arrives meanwhile is assessed next."""

    async def _run(self, case_id: int, force: bool) -> None:
        await self._assess(case_id, force)

    async def _assess(self, case_id: int, force: bool) -> None:
        inputs = read_inputs(case_id)
        if inputs is None or not inputs["has_information"]:
            return
        latest = latest_attempt(case_id)
        # ETA refreshes do not change the clinical/ack version, but they do make an attempt outdated.
        if latest and covers(latest, inputs["version"], inputs["baseline_version"], inputs["eta_at"]):
            if not (force and latest["status"] == "failed"):
                return

        now = utc_now_iso()
        assessment_id = run(
            """INSERT INTO risk_assessments
                   (case_id, based_on_version, baseline_version, input_eta_at, status, scorer_version, started_at, updated_at)
               VALUES (?, ?, ?, ?, 'processing', ?, ?, ?)""",
            (case_id, inputs["version"], inputs["baseline_version"], inputs["eta_at"], scorer_version(), now, now),
        )["id"]
        self.on_change(case_id)

        usage = {}
        try:
            output, usage = await asyncio.wait_for(score(inputs), timeout=SCORING_TIMEOUT_SECONDS)
            fields = check_numbers(interpret(output), inputs)
            error = fields.pop("error", None)
        except Exception as exc:
            print(f"❌ Assessment failed for case {case_id}: {exc!r}")
            fields, error = {"status": "failed"}, describe_error(exc)

        now = utc_now_iso()
        run(
            """UPDATE risk_assessments
               SET status = ?, preparation_category = ?, esi_level = ?, esi_reason = ?, risk_score = ?,
                   risk_reason = ?, meaningful_change = ?, summary = ?,
                   change_explanation = ?, missing_information = ?, review_reason = ?, error = ?,
                   input_tokens = ?, output_tokens = ?, latency_ms = ?, cost_usd = ?,
                   completed_at = ?, updated_at = ?
               WHERE id = ?""",
            (fields["status"], fields.get("preparation_category"), fields.get("esi_level"), fields.get("esi_reason"),
             fields.get("risk_score"), fields.get("risk_reason"), fields.get("meaningful_change"),
             fields.get("summary"), fields.get("change_explanation"), fields.get("missing_information"),
             fields.get("review_reason"), error, usage.get("input_tokens"), usage.get("output_tokens"),
             usage.get("latency_ms"), estimate_cost(openai_config.SCORING_MODEL, usage), now, now, assessment_id),
        )
        self.on_change(case_id)


runner = AssessmentRunner()


def fail_interrupted_assessments() -> None:
    """
    Called at startup: assessments in flight when the server stopped will never finish on their own.
    Their cases' updated_at moves too, so screens replace the rows they hold (still "Processing…").
    """
    now = utc_now_iso()
    with get_db() as conn:
        changed = conn.execute(
            """UPDATE risk_assessments SET status = 'failed', error = 'interrupted by a server restart',
                   completed_at = ?, updated_at = ?
               WHERE status = 'processing' RETURNING case_id""",
            (now, now),
        ).fetchall()
        affected = {row["case_id"] for row in changed}
        conn.executemany("UPDATE cases SET updated_at = ? WHERE id = ?", [(now, case_id) for case_id in affected])
    if changed:
        print(f"⚠️  Marked {len(changed)} interrupted risk assessment(s) as failed")


def unassessed_open_cases() -> list:
    """
    Open cases whose current information has no usable assessment: never assessed, queued when
    the server stopped, interrupted, or failed. Nothing else would re-trigger them after a restart.
    """
    open_cases = query(
        """SELECT c.id, c.info_version, c.eta_at,
                  (SELECT MAX(ack.info_version) FROM case_acknowledgments ack
                   WHERE ack.case_id = c.id AND ack.info_version < c.info_version) AS baseline_version
           FROM cases c WHERE c.status = 'active' ORDER BY c.id"""
    )
    usable = {}
    for attempt in query(f"""SELECT {ASSESSMENT_COLUMNS} FROM risk_assessments
                             WHERE status IN ('completed', 'needs_review')
                               AND case_id IN (SELECT id FROM cases WHERE status = 'active')"""):
        usable.setdefault(attempt["case_id"], []).append(attempt)
    return [case["id"] for case in open_cases
            if not any(covers(a, case["info_version"], case["baseline_version"], case["eta_at"])
                       for a in usable.get(case["id"], []))]
