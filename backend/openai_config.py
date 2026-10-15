"""The OpenAI client and model settings used by transcription, assessment and extraction.

Read once at import, after main.py has loaded .env. Callers read openai_client from this module when
they make a call, so tests can replace it with a fake or None.
"""
import os

from openai import OpenAI

# None when OPENAI_API_KEY is unset; the calls that need it then fail visibly.
openai_api_key = os.getenv("OPENAI_API_KEY")
# One SDK retry at most: with the default two, a call could outlast the caller's own timeout
# and keep a worker thread busy after the caller gave up. Transcription retries on its own.
OPENAI_MAX_RETRIES = 1
openai_client = OpenAI(api_key=openai_api_key, max_retries=OPENAI_MAX_RETRIES) if openai_api_key else None
# gpt-4 shuts down 2026-10-23. gpt-6-luna is the cheapest current model and the one evals/ uses.
SCORING_MODEL = os.getenv("OPENAI_SCORING_MODEL", "gpt-6-luna")
# whisper-1 is deprecated and misheard short live segments ("awake and" -> "Awaken");
# gpt-transcribe got them right in a side-by-side test and costs less per minute.
TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-transcribe")
# Sent only when set: models without reasoning reject the parameter. Blank it for those.
SCORING_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "none")
# A hung call would otherwise hold a worker for the client's 10-minute default.
SCORING_REQUEST_TIMEOUT_SECONDS = 40


def scoring_extra_body() -> dict:
    """Newer models take max_completion_tokens and a reasoning effort, not temperature/max_tokens."""
    body = {"max_completion_tokens": 1000}
    if SCORING_REASONING_EFFORT:
        body["reasoning_effort"] = SCORING_REASONING_EFFORT
    return body
