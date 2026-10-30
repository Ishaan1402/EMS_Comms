"""Build the pitch and jury videos from slides, screen clips and each speaker's recorded take.

Every slide runs as long as its speaker's take (recordings/<video>-<NN>-<name>.mp4, trimmed and cleaned by
takes.py). Screen clips start on the words that introduce them ("at": a phrase from the script), the demo
replay is time-warped so each event lands on the sentence that describes it, slides cross-fade into each
other, and each person gets a name caption the first time they appear. A slide whose take hasn't been
recorded yet gets a temporary macOS `say` voice and a grey camera box, so the cut can be watched early.

    python3 build.py jury            # -> out/jury.mp4      (EIR_WHISPER=1 for word-accurate cues)
    python3 build.py pitch
    python3 build.py jury --only 8   # one slide, for checking
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

import takes
from takes import W, H, FPS, run, find, prepare, estimate_words, find_phrase

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SAS = REPO / "demo/evidence/2026-10-27-sas-footage"
DEMO = REPO / "demo/evidence/2026-10-28-demo-replay/demo_replay_medic_doctor.mp4"
CLIPS, STOCK, OUT = HERE / "clips", HERE / "stock", HERE / "out"
WPM = 150                       # placeholder voice speed
CAM_W, CAM_H, CAM_M = 448, 252, 36
CAM_STYLE = os.environ.get("EIR_CAM", "window")   # window (framed picture-in-picture) or cutout (person only)
CUT_H = 400; CUT_W = CUT_H * 16 // 9
XFADE = 0.35                    # cross-fade between slides
LEAD, TAIL = 0.5, 0.45         # breathing room before and after each slide's speech


def sas(name):
    return str(SAS / name)


def slide(video, n, dur=None, at=None):
    return {"slide": str(HERE / "slides" / video / f"{n:02d}.png"), "dur": dur, "at": at}


def clip(path, a, b, at=None):
    return {"clip": str(path), "in": a, "out": b, "at": at}


# Demo replay events (seconds into demo_replay_medic_doctor.mp4) and the narration that should land on them.
DEMO_SYNC = [
    ("Now the medic just talks", 0.3),
    ("If I hover over one", 14.5),
    ("A moment later, the AI suggests", 18.0),
    ("The doctor acknowledges. Back on the medic's screen", 28.5),
    ("Now something goes wrong", 66.0),
    ("flags it in red on both screens", 70.0),
    ("The crew sends the update as text", 77.0),
    ("Medic 2 and Medic 10 are getting worse", 112.0),
    ("A \"new\" tag marks exactly what changed", 122.5),
    ("Then the medic calls a sepsis alert", 134.0),
    ("Medic 8 jumps straight to the top", 137.5),
    ("The doctor acknowledges, and the medic sees it", 144.5),
    ("change the ESI level with one tap", 149.0),
    ("exports as a standard FHIR file", 153.0),
]
DEMO_END = 162.5

# Each entry: script slide number, who speaks, how they appear, what's on screen.
# who: a name, "team", or a list (one after another). mode: corner | full | vo | full-tail | vo-head | none.
# Visuals: dur=None fills the time left; "at" starts it on that phrase of the narration.
JURY = [
    (1, "ishaan", "vo", [{"stock": ["lights", "radio"]}, slide("jury", 1, at="seventy thousand")]),
    (2, "ishaan", "full-tail", [slide("jury", 2)]),
    (3, "team", "full", [slide("jury", 3)]),
    (4, "dj", "corner", [slide("jury", 4)]),
    (5, "ishaan", "corner", [slide("jury", 5)]),
    (6, "anand", "vo", [slide("jury", 6), {"demo": True, "at": "Now the medic just talks"}]),
    (7, "mayank", "corner", [slide("jury", 7), {"optional_clip": str(CLIPS / "datamaker.mp4"), "at": "We also used SAS Data Maker"}, slide("jury", 7)]),
    (8, "mayank", "corner", [slide("jury", 8, 2.5), clip(CLIPS / "pyclipA.mp4", 0, 4), clip(sas("shot1_live_app_to_sas.mp4"), 2, 35, at="Here's the app on the left"), slide("jury", 8)]),
    (9, "vrishank", "corner", [slide("jury", 9, 2.5), clip(CLIPS / "pyclipB.mp4", 0, 4), clip(sas("shot3_sas_studio_audit.mp4"), 2, 14),
                               clip(sas("shot2_critical_patients_and_outcomes.mp4"), 45, 64, at="including 1,683 US ambulance visits"), slide("jury", 9)]),
    (10, "vrishank", "corner", [slide("jury", 10, 2.5), clip(sas("shot4_fairness.mp4"), 8, 15), clip(sas("shot4_fairness.mp4"), 39, 47), slide("jury", 10)]),
    (11, "mayank", "corner", [slide("jury", 11, 2.5), clip(sas("shot7_model_studio_baseline.mp4"), 1, 9),
                              clip(sas("shot7_model_studio_baseline.mp4"), 41, 53, at="At the same false-alarm level"), slide("jury", 11)]),
    (12, "mayank", "corner", [slide("jury", 12, 2), clip(sas("shot8_model_manager_card.mp4"), 2, 10),
                              clip(sas("shot10_model_manager_monitoring.mp4"), 32, 38, at="And it monitors the AI"), slide("jury", 12)]),
    (13, "vrishank", "corner", [slide("jury", 13), clip(sas("shot9_intelligent_decisioning.mp4"), 2, 25, at="If the AI gives a level"), slide("jury", 13)]),
    (14, "mayank", "corner", [slide("jury", 14), {"optional_clip": sas("shot11_high_impact.mp4"), "at": "An average US emergency department"},
                              slide("jury", 14, at="And if Eir helps a quarter of septic")]),
    (15, "dj", "corner", [slide("jury", 15)]),
    (16, "vrishank", "corner", [slide("jury", 16), clip(sas("shot5_speed_and_cost.mp4"), 0, 12, at="SAS counts"),
                                clip(sas("shot2_critical_patients_and_outcomes.mp4"), 17, 26, at="Of 154 ambulance patients"), slide("jury", 16)]),
    (17, "dj", "corner", [slide("jury", 17)]),
    (18, ["ishaan", "team"], ["vo", "full"], [{"stock": ["stretcher"]}, slide("jury", 18, at="We built Eir so the ER team")]),
    (19, None, "none", [slide("jury", 19, 3)]),
]
PITCH = [
    (1, "ishaan", "vo-head", [{"stock": ["lights", "radio", "stretcher"]}, slide("pitch", 1, at="Across the country")]),
    (2, "ishaan", "corner", [slide("pitch", 2)]),
    (3, "ishaan", "corner", [slide("pitch", 3)]),
    (4, "anand", "corner", [slide("pitch", 4), slide("pitch", 5, at="The medic asks the patient's permission"),
                            slide("pitch", 6, at="Then the crew calls a sepsis alert"), slide("pitch", 7, at="The doctor acknowledges")]),
    (5, "mayank", "corner", [slide("pitch", 8, 4), clip(sas("shot1_live_app_to_sas.mp4"), 2, 6), clip(sas("shot2_critical_patients_and_outcomes.mp4"), 45, 49),
                             clip(sas("shot4_fairness.mp4"), 39, 43), slide("pitch", 8, at="The AI only suggests")]),
    (6, "vrishank", "corner", [slide("pitch", 9)]),
    (7, ["dj", "team"], ["corner", "full"], [slide("pitch", 10)]),
    (8, None, "none", [slide("pitch", 11, 3)]),
]
TEAM_ORDER = ["ishaan", "vrishank", "dj", "anand", "mayank"]
TEAM_STARTS = {}  # where each person starts inside a stitched team intro
TEAM_LINES = {"jury-18": "We're InTheBud. Thank you.", "pitch-07": "We're InTheBud, and this is Eir. Thank you."}
ROLES = {"ishaan": ("Ishaan", "Team lead · built the Eir platform"), "vrishank": ("Vrishank", "AI evaluation"),
         "dj": ("DJ", "Vital-sign extraction · patient queue"), "anand": ("Anand", "Live transcription · clinician interface"),
         "mayank": ("Mayank", "SAS Viya pipeline · dashboards")}
# Sentences to cut out of a take without re-recording (for trimming to time), e.g.
# DROPS = {"jury-16-vrishank": ["Of 154 ambulance patients that experts rated critical"]}
DROPS = json.loads((HERE / "drops.json").read_text()) if (HERE / "drops.json").exists() else {}
SKIP = json.loads((HERE / "skip.json").read_text()) if (HERE / "skip.json").exists() else {}


def duration(path):
    return takes.probe(path)["duration"]


def font(size, bold=False):
    return ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", size, index=1 if bold else 0)


def card(path, title, sub=""):
    im = Image.new("RGB", (W, H), "#0F1B2D")
    d = ImageDraw.Draw(im)
    d.text((120, 440), title, font=font(72, True), fill="white")
    d.text((120, 540), sub, font=font(36), fill="#93C5FD")
    im.save(path)


def caption_png(path, name, role, wide):
    """A lower-third name caption: dark card, name bold, role below. Smaller when it sits inside the corner camera."""
    s = 1.0 if wide else 0.74
    f1, f2 = font(int(30 * s), True), font(int(22 * s))
    tw = max(f1.getlength(name), f2.getlength(role)) + 48 * s
    im = Image.new("RGBA", (int(tw), int(92 * s)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=int(12 * s), fill=(15, 27, 45, 225))
    d.rectangle([0, int(14 * s), int(5 * s), int(78 * s)], fill=(37, 99, 235, 255))
    d.text((int(24 * s), int(12 * s)), name, font=f1, fill="white")
    d.text((int(24 * s), int(52 * s)), role, font=f2, fill=(203, 213, 225))
    im.save(path)
    return im.size


def placeholder_cam(path, who):
    im = Image.new("RGB", (W, H), "#475569")
    d = ImageDraw.Draw(im)
    d.text((420, 440), who.capitalize(), font=font(140, True), fill="white")
    d.text((420, 620), "camera goes here (temporary voice)", font=font(60), fill="#CBD5E1")
    im.save(path)


def placeholder_take(tag, who, text, tmp):
    wav, aiff = tmp / f"{tag}.wav", tmp / f"{tag}.aiff"
    run(["say", "-v", "Samantha", "-r", str(WPM + 20), "-o", aiff, text or "."])
    run(["ffmpeg", "-v", "error", "-y", "-i", aiff, "-af", "adelay=150|150,apad=pad_dur=0.2", "-ar", "48000", "-ac", "2", wav])
    still = tmp / f"{tag}-cam.png"
    placeholder_cam(still, "Team" if who == "team" else who)
    out = tmp / f"{tag}-ph.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-r", str(FPS), "-i", still, "-i", wav, "-shortest",
         "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "48000", out])
    return out, estimate_words(text, duration(out))


def take_for(video, n, who, lines, tmp):
    """(path, words, real) for one speaker on one slide."""
    tag = f"{video}-{n:02d}-{who}"
    f = find(tag)
    text = " ".join(lines)
    if f is None and who == "team" and any(find(f"{video}-{n:02d}-{p}") for p in TEAM_ORDER):
        # Individual intro takes one after another; anyone missing gets the temporary voice for their line.
        parts, words, t = [], [], 0.0
        for p in TEAM_ORDER:
            g = find(f"{video}-{n:02d}-{p}")
            mine = " ".join(l.split(": ", 1)[1] for l in lines if l.lower().startswith(p + ":"))
            if g is not None:
                path, meta = prepare(g, tmp, DROPS.get(f"{video}-{n:02d}-{p}", ()))
                ws = meta["words"] or estimate_words(mine, duration(path))
            else:
                path, ws = placeholder_take(f"{video}-{n:02d}-{p}", p, mine, tmp)
            d = duration(path)
            TEAM_STARTS[p] = t
            words += [[w[0], w[1] + t, w[2] + t] for w in ws]
            parts.append(path)
            t += d
        out = tmp / f"{tag}-cat.mp4"
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for q in parts:
            cmd += ["-i", q]
        chain = "".join(f"[{k}:v]scale={W}:{H},fps={FPS},format=yuv420p,setsar=1[v{k}];[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}];" for k in range(len(parts)))
        chain += "".join(f"[v{k}][a{k}]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=1[v][a]"
        run(cmd + ["-filter_complex", chain, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", out])
        return out, words, all(find(f"{video}-{n:02d}-{p}") for p in TEAM_ORDER)
    if f is not None:
        path, meta = prepare(f, tmp, DROPS.get(tag, ()))
        return path, meta["words"] or estimate_words(text, duration(path)), True
    if who == "team" and n == 3 and video == "jury":
        text = " ".join(l.split(": ", 1)[1] for l in lines)
    path, words = placeholder_take(tag, who, text, tmp)
    return path, words, False


def schedule(visuals, total, words, tmp, video, n):
    """Concrete pieces [kind, src, start_in_src, dur, extra] that exactly fill `total` seconds."""
    base = next((v for v in visuals if "slide" in v), None)
    items = []
    for v in visuals:
        at = None
        if v.get("at"):
            hit = find_phrase(words, v["at"])
            at = hit[0] if hit else None
            if at is None:
                print(f"  ! cue not found, timing it instead: {v['at']!r}", flush=True)
        if "stock" in v:
            files = [next(iter(sorted(STOCK.glob(f"{s}*.mp4"))), None) for s in v["stock"]]
            if all(files):
                for k, f in enumerate(files):
                    items.append({"kind": "video", "src": str(f), "in": 0, "dur": None, "max": duration(f), "at": at if k == 0 else None})
            else:
                ph = tmp / f"{video}-{n:02d}-stock.png"
                card(ph, "Stock footage", " + ".join(v["stock"]))
                items.append({"kind": "image", "src": str(ph), "dur": None, "at": at})
        elif "demo" in v:
            items.append({"kind": "demo", "src": str(DEMO), "dur": None, "at": at})
        elif "optional_clip" in v:
            if Path(v["optional_clip"]).exists():
                items.append({"kind": "video", "src": v["optional_clip"], "in": 0, "dur": duration(v["optional_clip"]), "at": at})
        elif "clip" in v:
            items.append({"kind": "video", "src": v["clip"], "in": v["in"], "dur": v["out"] - v["in"], "at": at})
        else:
            items.append({"kind": "image", "src": v["slide"], "dur": v["dur"], "at": at})
    # Groups run from one cue to the next; inside a group fixed pieces keep their length and the rest share the time.
    groups, cur = [], []
    for it in items:
        if it["at"] is not None and cur:
            groups.append(cur)
            cur = []
        cur.append(it)
    groups.append(cur)
    pieces, t = [], 0.0
    for gi, g in enumerate(groups):
        start = max(t, g[0]["at"] if g[0]["at"] is not None else t)
        if start > t + 0.05:  # a gap before this cue: hold the previous picture (or the slide)
            if pieces and pieces[-1][0] in ("image", "demo") or (pieces and pieces[-1][3] is None):
                pieces[-1][3] += start - t
            elif base:
                pieces.append(["image", base["slide"], 0, start - t, None])
        t = start
        end = total
        if gi + 1 < len(groups) and groups[gi + 1][0]["at"] is not None:
            end = min(total, max(t + 0.5, groups[gi + 1][0]["at"]))
        window = end - t
        fixed = sum(it["dur"] for it in g if it["dur"] is not None)
        fills = [it for it in g if it["dur"] is None]
        spare = window - fixed
        out = []
        for it in g:
            d = it["dur"]
            if d is None:
                d = max(0.0, spare) / len(fills)
                if it.get("max"):
                    d = min(d, it["max"])
            out.append([it["kind"], it["src"], it.get("in", 0), d, it])
        used = sum(p[3] for p in out)
        if used > window:  # too long: shorten from the end, never under 1 s
            over = used - window
            for p in reversed(out):
                cut = min(over, max(0, p[3] - 1.0))
                p[3] -= cut
                over -= cut
                if over <= 0.01:
                    break
        elif window - used > 0.05:  # too short (stock ran out, no fill): back to the slide
            if base and (not out or out[-1][0] != "image"):
                out.append(["image", base["slide"], 0, window - used, None])
            elif out:
                out[-1][3] += window - used
        pieces += [p for p in out if p[3] > 0.04]
        t = end
    return pieces


def demo_map(words, offset, length):
    """Piecewise map from narration time (within this piece) to demo time, from DEMO_SYNC cues."""
    pts = [(0.0, DEMO_SYNC[0][1])]
    last_t = 0.0
    for phrase, demo_t in DEMO_SYNC:
        hit = find_phrase(words, phrase, after=offset + last_t)
        if hit:
            t = hit[0] - offset
            if 0 < t < length and t > last_t + 0.3 and demo_t > pts[-1][1]:
                pts.append((t, demo_t))
                last_t = t
    pts.append((length, DEMO_END))
    return pts


def render_demo(src, dur, words, offset, out_stem, tmp):
    """The replay, warped so each cue lands on its sentence. Big gaps jump-cut; short ones slow down or hold."""
    pts = demo_map(words, offset, dur)
    parts = []
    for k in range(len(pts) - 1):
        (t0, d0), (t1, d1) = pts[k], pts[k + 1]
        vo, span = t1 - t0, max(0.04, d1 - d0)
        if vo <= 0.04:
            continue
        speed = span / vo
        if speed > 2.5:  # skip ahead: show the run-up to the next event at 1.5x
            d0, span, speed = d1 - vo * 1.5, vo * 1.5, 1.5
        speed = max(0.6, speed)
        part = tmp / f"{out_stem}-demo{k}.mp4"
        hold = max(0.0, vo - span / speed)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, d0):.3f}", "-i", src, "-t", f"{span:.3f}", "-an",
             "-vf", f"setpts=PTS/{speed:.4f},fps={FPS},format=yuv420p,setsar=1,tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f}",
             "-t", f"{vo:.3f}", "-c:v", "libx264", "-crf", "16", "-preset", "fast", part])
        parts.append(part)
    return parts


def render_visual(pieces, out, tmp, words):
    parts, t = [], 0.0
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white,fps={FPS},format=yuv420p,setsar=1"
    for i, (kind, src, start, dur, _) in enumerate(pieces):
        part = tmp / f"{out.stem}-v{i}.mp4"
        if kind == "image":
            run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-r", str(FPS), "-t", f"{dur:.3f}", "-i", src, "-vf", vf,
                 "-c:v", "libx264", "-crf", "16", "-preset", "fast", part])
            parts.append(part)
        elif kind == "demo":
            parts += render_demo(src, dur, words, t, f"{out.stem}-v{i}", tmp)
        else:
            length = max(0.1, duration(src) - start)
            hold = max(0.0, dur - length)
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", src, "-t", f"{min(length, dur):.3f}", "-an",
                 "-vf", f"{vf},tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f}", "-t", f"{dur:.3f}",
                 "-c:v", "libx264", "-crf", "16", "-preset", "fast", part])
            parts.append(part)
        t += dur
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


def compose(visual, speakers, captions, out, tmp):
    """Speakers over the visual. speakers: (path, mode, start, dur, switch_at). captions: (png, x, y, start, end)."""
    total = duration(visual)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", visual]
    for s in speakers:
        cmd += ["-i", s[0]]
    mask, frame = tmp / "mask.png", tmp / "frame.png"
    if not mask.exists():
        m = Image.new("L", (CAM_W, CAM_H), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, CAM_W - 1, CAM_H - 1], radius=22, fill=255)
        m.save(mask)
        f = Image.new("RGBA", (CAM_W + 8, CAM_H + 8), (0, 0, 0, 0))
        ImageDraw.Draw(f).rounded_rectangle([0, 0, CAM_W + 7, CAM_H + 7], radius=26, outline=(255, 255, 255, 255), width=4)
        ImageDraw.Draw(f).rounded_rectangle([3, 3, CAM_W + 4, CAM_H + 4], radius=23, outline=(15, 27, 45, 90), width=1)
        f.save(frame)
    cmd += ["-loop", "1", "-i", mask]
    mi = len(speakers) + 1
    for c in captions:
        cmd += ["-loop", "1", "-i", c[0]]
    fc, last, audios = [], "[0:v]", []
    for i, (path, mode, start, dur, switch) in enumerate(speakers, 1):
        end = start + dur
        corner = mode in ("corner", "full-tail", "vo-head")
        full = mode in ("full", "full-tail")
        base = f"[{i}:v]scale={W}:{H},fps={FPS},setpts=PTS-STARTPTS+{start}/TB"
        if corner and full:
            fc.append(f"{base},split[f{i}][c{i}]")
        elif corner:
            fc.append(f"{base}[c{i}]")
        elif full:
            fc.append(f"{base}[f{i}]")
        if corner:
            a = switch if mode == "vo-head" else start
            b = switch if mode == "full-tail" else end
            if CAM_STYLE == "cutout":  # the person alone, no background, standing on the bottom-right edge
                pm = takes.person_mask(path)
                fc.append(f"movie={pm},format=gray,scale={W}:{H},boxblur=2:1,setpts=PTS-STARTPTS+{start}/TB[pm{i}];[c{i}][pm{i}]alphamerge,"
                          f"scale=-2:{CUT_H}[cr{i}]")
                fc.append(f"{last}[cr{i}]overlay=W-w+{CUT_W // 6}:H-h:enable='between(t,{a:.3f},{b:.3f})':eof_action=pass[o{i}a]")
            else:
                fc.append(f"[c{i}]scale={CAM_W}:{CAM_H}[cs{i}];[{mi}:v]fps={FPS},setpts=PTS-STARTPTS+{start}/TB[m{i}];[cs{i}][m{i}]alphamerge[cr{i}]")
                fc.append(f"{last}[cr{i}]overlay=W-w-{CAM_M}:H-h-{CAM_M}:enable='between(t,{a:.3f},{b:.3f})':eof_action=pass[o{i}p]")
                fc.append(f"movie={frame},loop=-1:1,setpts=N/{FPS}/TB[fr{i}];[o{i}p][fr{i}]overlay=W-w-{CAM_M - 4}:H-h-{CAM_M - 4}:enable='between(t,{a:.3f},{b:.3f})'[o{i}a]")
            last = f"[o{i}a]"
        if full:
            a = start if mode == "full" else switch
            fc.append(f"{last}[f{i}]overlay=0:0:enable='between(t,{a:.3f},{end:.3f})':eof_action=pass[o{i}b]")
            last = f"[o{i}b]"
        fc.append(f"[{i}:a]adelay={int(start * 1000)}|{int(start * 1000)},apad[a{i}]")
        audios.append(f"[a{i}]")
    for k, (png, x, y, a, b) in enumerate(captions):
        idx = mi + 1 + k
        fc.append(f"[{idx}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.3:alpha=1,fade=t=out:st={b - 0.3:.2f}:d=0.3:alpha=1[cap{k}]")
        fc.append(f"{last}[cap{k}]overlay={x}:{y}:enable='between(t,{a:.3f},{b:.3f})'[oc{k}]")
        last = f"[oc{k}]"
    fc.append(f"{last}format=yuv420p[v]")
    if audios:
        fc.append(f"{''.join(audios)}amix=inputs={len(audios)}:normalize=0,atrim=0:{total:.3f}[a]")
    else:
        fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[a]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out]
    run(cmd)
    return out


def join(segs, out):
    """All slides into one file, cross-fading picture and sound between them."""
    if len(segs) == 1:
        run(["ffmpeg", "-v", "error", "-y", "-i", segs[0], "-c", "copy", "-movflags", "+faststart", out])
        return
    durs = [duration(s) for s in segs]
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for s in segs:
        cmd += ["-i", s]
    fc, v, a, t = [], "[0:v]", "[0:a]", durs[0]
    for k in range(1, len(segs)):
        off = t - XFADE
        fc.append(f"{v}[{k}:v]xfade=transition=fade:duration={XFADE}:offset={off:.3f}[v{k}]")
        fc.append(f"{a}[{k}:a]acrossfade=d={XFADE}[a{k}]")
        v, a, t = f"[v{k}]", f"[a{k}]", t + durs[k] - XFADE
    fc.append(f"{a}alimiter=limit=0.95[aout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", v, "-map", "[aout]", "-c:v", "libx264", "-b:v", "5800k", "-maxrate", "8000k", "-bufsize", "12000k", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out]
    run(cmd)


def build(video, only=None):
    script = [s for s in json.load(open(HERE / "script.json")) if s["video"].lower().startswith(video)]
    plan = JURY if video == "jury" else PITCH
    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"eir-{video}-"))
    segs, report, introduced = [], [], set()
    for n, who, mode, visuals in plan:
        if (only and n not in only) or n in SKIP.get(video, []):
            continue
        s = script[n - 1]
        lines = s["script"]
        whos = who if isinstance(who, list) else [who]
        modes = mode if isinstance(mode, list) else [mode]
        tag = f"{video}-{n:02d}"
        speakers, words, captions, t, status = [], [], [], LEAD, []
        for w, m in zip(whos, modes):
            if w is None:
                continue
            if w == "team" and not (n == 3 and video == "jury"):
                seg_lines = [TEAM_LINES[tag]]
            elif len(whos) > 1:
                seg_lines = [l.replace(" " + TEAM_LINES.get(tag, "~"), "").replace(TEAM_LINES.get(tag, "~"), "") for l in lines]
                seg_lines = [l for l in seg_lines if l.strip()]
            else:
                seg_lines = lines
            path, ws, real = take_for(video, n, w, seg_lines, tmp)
            d = duration(path)
            switch = t
            if m == "full-tail":   # last paragraph full screen
                hit = find_phrase(ws, seg_lines[-1][:60])
                switch = t + (hit[0] if hit else d * 0.75)
            if m == "vo-head":     # voice-only under the footage, then the corner camera
                hit = find_phrase(ws, seg_lines[-1][:60])
                switch = t + (hit[0] if hit else d * 0.7)
            speakers.append((path, m, t, d, switch))
            words += [[x[0], x[1] + t, x[2] + t] for x in ws]
            status.append(f"{w}{'' if real else ' (temp voice)'}")
            # Name caption the first time someone appears on camera.
            names = TEAM_ORDER if w == "team" else [w]
            for p in names:
                if p in introduced or m in ("vo",) or p not in ROLES:
                    continue
                if w == "team":
                    hit = find_phrase(ws, f"I'm {ROLES[p][0]}") or find_phrase(ws, f"name is {ROLES[p][0]}")
                    if hit:
                        a = t + hit[0]
                    elif p in TEAM_STARTS:
                        a = t + TEAM_STARTS[p] + 0.2
                    else:
                        continue
                else:
                    a = (switch if m == "vo-head" else t) + 0.3
                png = tmp / f"cap-{p}.png"
                full_now = m == "full" or (m == "full-tail" and a >= switch) or w == "team"
                cw, ch = caption_png(png, *ROLES[p], wide=full_now)
                # Full screen: bottom-left lower third. Corner camera: inside the camera window, bottom-left.
                x, y = (60, H - ch - 60) if full_now else (W - CAM_W - CAM_M + 12, H - CAM_M - ch - 12)
                captions.append((png, x, y, a, a + (3.5 if w == "team" else 4.5)))
                introduced.add(p)
            t += d + 0.3
        total = t - 0.3 + TAIL if speakers else (visuals[0]["dur"] or 3)
        pieces = schedule(visuals, total, words, tmp, video, n)
        vis = render_visual(pieces, tmp / f"{tag}-visual.mp4", tmp, words)
        seg = compose(vis, speakers, captions, tmp / f"{tag}.mp4", tmp)
        segs.append(seg)
        report.append((n, s["name"], round(duration(seg), 1), ", ".join(status) or "-"))
        print(f"{tag} {s['name']}: {duration(seg):.1f}s  {report[-1][3]}", flush=True)
    out = OUT / (f"InTheBud-{video.capitalize()}.mp4" if not only else f"{video}-only-{'-'.join(map(str, only))}.mp4")
    join(segs, out)
    total = duration(out)
    print(f"\n{out}  {int(total // 60)}:{total % 60:04.1f}")
    return out, report


if __name__ == "__main__":
    video = sys.argv[1]
    only = None
    if "--only" in sys.argv:
        only = [int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")]
    build(video, only)
