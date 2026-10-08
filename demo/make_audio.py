"""Render a demo scenario's reports to speech, one WAV per report, for replay.py.

    python3 demo/make_audio.py                     # macOS `say`, offline and free
    python3 demo/make_audio.py --engine openai     # OpenAI voice, more natural for the video (OPENAI_API_KEY)

Files go to demo/audio/<source_case_id>-<report>.wav (mono WAV). Existing files are kept
unless --force is given, so re-running is cheap.
"""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

DEMO_DIR = Path(__file__).parent
DEFAULT_SCENARIO = DEMO_DIR / "three_patients.json"
DEFAULT_AUDIO_DIR = DEMO_DIR / "audio"


def audio_path(audio_dir: Path, source_case_id: str, report: str) -> Path:
    return audio_dir / f"{source_case_id}-{report}.wav"


def render_say(text: str, out: Path, voice: str = None) -> None:
    if not shutil.which("say"):
        raise SystemExit("`say` is only available on macOS; use --engine openai instead.")
    command = ["say", "-r", "180", "-o", str(out), "--data-format=LEI16@16000"]
    if voice:
        command += ["-v", voice]
    subprocess.run(command + [text], check=True)


def render_openai(text: str, out: Path, voice: str = None) -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(DEMO_DIR.parent / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY (in .env) to use --engine openai.")
    response = OpenAI().audio.speech.create(
        model="gpt-4o-mini-tts", voice=voice or "onyx", input=text, response_format="wav",
        extra_body={"instructions": "A calm, experienced paramedic giving a radio report to the hospital."},
    )
    out.write_bytes(response.content)


ENGINES = {"say": render_say, "openai": render_openai}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--out", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--engine", choices=ENGINES, default="say")
    parser.add_argument("--voice", help="say: a voice from `say -v '?'`; openai: e.g. alloy, onyx")
    parser.add_argument("--force", action="store_true", help="re-render files that already exist")
    args = parser.parse_args()

    scenario = json.loads(args.scenario.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    render = ENGINES[args.engine]
    for patient in scenario["patients"]:
        for report, text in patient["reports"].items():
            out = audio_path(args.out, patient["source_case_id"], report)
            if out.exists() and not args.force:
                print(f"exists   {out}")
                continue
            render(text, out, args.voice)
            print(f"rendered {out}")


if __name__ == "__main__":
    main()
