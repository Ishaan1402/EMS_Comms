"""Speaker takes: find, clean, trim and time them.

A take is one person's clip for one slide (recordings/<video>-<NN>-<name>.<ext>). prepare() returns a
1920x1080/30 fps/48 kHz copy with the dead air at both ends trimmed, the voice cleaned and levelled, and
the words with their times, so the edit can cut on phrases instead of guesses.

Word times come from OpenAI Whisper when EIR_WHISPER=1 (cached next to the take); otherwise they are
spread evenly over the speech, which is close enough for slide timing but not for phrase cues.
"""
import hashlib
import json
import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REC = HERE / "recordings"
CACHE = REC / ".cache"
EXTS = (".mp4", ".mov", ".m4v", ".MP4", ".MOV", ".M4V")
W, H, FPS = 1920, 1080, 30
PAD_IN, PAD_OUT = 0.25, 0.35


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"failed: {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r.stdout + r.stderr


def probe(path):
    out = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path]))
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in out["streams"] if s["codec_type"] == "audio"), None)
    w, h = (int(v["width"]), int(v["height"])) if v else (0, 0)
    rot = 0
    if v:
        rot = int(v.get("tags", {}).get("rotate", 0) or 0)
        for sd in v.get("side_data_list", []):
            if "rotation" in sd:
                rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"duration": float(out["format"]["duration"]), "width": w, "height": h, "has_video": bool(v), "has_audio": bool(a)}


def find(tag):
    """recordings/<tag>.* first, then a loose match (case, - or _ or space) in recordings/ and ~/Downloads."""
    for ext in EXTS:
        if (REC / (tag + ext)).exists():
            return REC / (tag + ext)
    key = re.sub(r"[^a-z0-9]", "", tag.lower())
    for folder in (REC,):  # Downloads files are brought in by identify.py / intake.py, never picked up directly
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.suffix in EXTS and re.sub(r"[^a-z0-9]", "", f.stem.lower()) == key:
                return f
    return None


def speech_bounds(path, duration):
    """First and last moment of speech, from silencedetect."""
    log = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "highpass=f=100,silencedetect=noise=-38dB:d=0.35", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    first, last = 0.0, duration
    if ends and starts and starts[0] < 0.05:
        first = ends[0]
    if starts and (not ends or starts[-1] > ends[-1] or ends[-1] >= duration - 1.5):
        last = starts[-1]  # trailing silence (newer ffmpeg also reports its end at the end of the file)
    if last - first < 0.5:
        return 0.0, duration
    return first, last


def _cache_key(path, extra=""):
    st = Path(path).stat()
    return hashlib.sha1(f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime}|{extra}".encode()).hexdigest()[:16]


def prepare(path, tmp, drop=()):
    """Clean, trimmed, timed copy of a take. drop: phrases to cut out of it (needs Whisper word times)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    info = probe(path)
    key = _cache_key(path, "|".join(drop))
    out = CACHE / f"{Path(path).stem}-{key}.mp4"
    meta = CACHE / f"{Path(path).stem}-{key}.json"
    if out.exists() and meta.exists():
        return out, json.loads(meta.read_text())
    a, b = speech_bounds(path, info["duration"])
    a, b = max(0.0, a - PAD_IN), min(info["duration"], b + PAD_OUT)
    portrait = info["height"] > info["width"]
    # Portrait phone video: crop a 16:9 band from the upper part, where the face is.
    crop = (f"crop=iw:iw*9/16:0:(ih-iw*9/16)*0.25,scale={W}:{H}" if portrait
            else f"crop=iw*0.92:ih*0.92:iw*0.04:ih*0.02,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")  # trims call-app name labels at the edges
    vf = f"{crop},fps={FPS},format=yuv420p,setsar=1" if info["has_video"] else None
    af = "highpass=f=80,afftdn=nf=-28,loudnorm=I=-16:TP=-1.5:LRA=9"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", path]
    if not info["has_video"]:
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x475569:s={W}x{H}:r={FPS}",
               "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", path, "-shortest", "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-vf", vf]
    cmd += ["-af", af, "-ar", "48000", "-ac", "2", "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", out]
    run(cmd)
    words = transcribe(out)
    if drop and words:
        out, words = cut_phrases(out, words, drop, tmp)
    meta.write_text(json.dumps({"words": words, "source": str(path), "trim": [a, b], "portrait": portrait}))
    return out, {"words": words, "source": str(path), "trim": [a, b], "portrait": portrait}


def transcribe(path):
    """[(word, start, end)] from Whisper, or None when EIR_WHISPER isn't on."""
    if os.environ.get("EIR_WHISPER") != "1":
        return None
    cached = Path(str(path) + ".words.json")
    if cached.exists():
        return json.loads(cached.read_text())
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    from openai import OpenAI
    wav = Path(str(path) + ".m4a")
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "48k", wav])
    with open(wav, "rb") as f:
        r = OpenAI().audio.transcriptions.create(model="whisper-1", file=f, response_format="verbose_json",
                                                 timestamp_granularities=["word"])
    words = [[w.word, round(w.start, 3), round(w.end, 3)] for w in r.words]
    cached.write_text(json.dumps(words))
    return words


def norm(word):
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("%", "percent"))


def estimate_words(text, duration):
    """Evenly spread words when there is no transcript (placeholder voice, or Whisper off)."""
    toks = text.split()
    if not toks:
        return []
    lead, tail = 0.05, 0.05
    span = max(0.1, duration - lead - tail)
    weights = [max(1, len(norm(t))) + 2 for t in toks]
    total, t, out = sum(weights), lead, []
    for tok, w in zip(toks, weights):
        d = span * w / total
        out.append([tok, round(t, 3), round(t + d, 3)])
        t += d
    return out


def find_phrase(words, phrase, after=0.0):
    """Start and end time of the best match for `phrase` in words (starting at or after `after`)."""
    target = [norm(w) for w in phrase.split() if norm(w)]
    toks = [norm(w[0]) for w in words]
    best, best_i = 0.0, None
    n = len(target)
    for i in range(len(toks)):
        if words[i][1] < after - 0.01:
            continue
        window = toks[i:i + n]
        score = SequenceMatcher(None, " ".join(window), " ".join(target)).ratio()
        if score > best:
            best, best_i = score, i
    if best_i is None or best < 0.6:
        return None
    j = min(len(words) - 1, best_i + n - 1)
    return words[best_i][1], words[j][2]


def cut_phrases(path, words, phrases, tmp):
    """Remove each phrase (a sentence to drop) from the take, with 60 ms fades at the joins."""
    spans = []
    for p in phrases:
        hit = find_phrase(words, p)
        if hit:
            spans.append((max(0, hit[0] - 0.08), hit[1] + 0.12))
    if not spans:
        return path, words
    spans.sort()
    dur = probe(path)["duration"]
    keep, t = [], 0.0
    for s, e in spans:
        if s > t:
            keep.append((t, s))
        t = max(t, e)
    if t < dur:
        keep.append((t, dur))
    parts = []
    for k, (s, e) in enumerate(keep):
        part = Path(tmp) / f"{Path(path).stem}-keep{k}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path,
             "-af", f"afade=t=in:d=0.06,afade=t=out:st={max(0, e - s - 0.06):.3f}:d=0.06",
             "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", part])
        parts.append(part)
    lst = Path(tmp) / f"{Path(path).stem}-keep.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = Path(str(path).replace(".mp4", "-cut.mp4"))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    new_words, shift, ki = [], 0.0, 0
    for s, e in keep:
        for w in words:
            if s <= w[1] < e:
                new_words.append([w[0], round(w[1] - s + shift, 3), round(min(w[2], e) - s + shift, 3)])
        shift += e - s
    Path(str(out) + ".words.json").write_text(json.dumps(new_words))
    return out, new_words


SEGMENT = HERE / "segment/segment"


def person_mask(path):
    """Grey mask video of the person (white) for a prepared take, made on the Neural Engine and cached."""
    out = Path(str(path).replace(".mp4", "-person.mp4"))
    if not out.exists():
        run([SEGMENT, path, out, "balanced"])
    return out
