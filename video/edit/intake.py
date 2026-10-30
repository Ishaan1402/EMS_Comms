"""Which takes are in, which are missing, and which look wrong.

    python intake.py          # looks in recordings/ and ~/Downloads (loose names like "Jury_08_Mayank.MOV" are fine)

For each expected take: found or missing, its length against the script's estimate, orientation, and
whether it has sound. Copies loosely named files from Downloads into recordings/ under the exact name.
"""
import json
import shutil
import subprocess
from pathlib import Path

import build
import takes

HERE = Path(__file__).resolve().parent
WPM = 145


def expected():
    script = json.load(open(HERE / "script.json"))
    for video, plan in (("jury", build.JURY), ("pitch", build.PITCH)):
        slides = [s for s in script if s["video"].lower().startswith(video)]
        for n, who, mode, _ in plan:
            whos = who if isinstance(who, list) else [who]
            for w in whos:
                if w is None:
                    continue
                lines = slides[n - 1]["script"]
                if w == "team":
                    if video == "jury" and n == 3:
                        for p in build.TEAM_ORDER:
                            mine = [l.split(": ", 1)[1] for l in lines if l.lower().startswith(p + ":")]
                            yield f"{video}-{n:02d}-{p}", p, " ".join(mine), "or the team take " + f"{video}-{n:02d}-team"
                        continue
                    yield f"{video}-{n:02d}-team", "team", build.TEAM_LINES[f"{video}-{n:02d}"], ""
                    continue
                text = " ".join(lines).replace(build.TEAM_LINES.get(f"{video}-{n:02d}", "~"), "")
                yield f"{video}-{n:02d}-{w}", w, text, ""


def main():
    rows, missing = [], {}
    team_take = {t: takes.find(t) for t in ("jury-03-team",)}
    for tag, who, text, note in expected():
        f = takes.find(tag)
        est = len(text.split()) / WPM * 60
        if f is None:
            if tag.startswith("jury-03-") and team_take["jury-03-team"]:
                continue
            missing.setdefault(who, []).append(tag)
            rows.append((tag, "MISSING", f"~{est:.0f} s expected {note}".strip()))
            continue
        if f.parent != takes.REC:
            takes.REC.mkdir(exist_ok=True)
            dest = takes.REC / (tag + f.suffix.lower())
            shutil.copy2(f, dest)  # a copy: the original in Downloads is left alone
            f = dest
        f.chmod(0o444)  # raw takes are read-only; the editor only ever writes to recordings/.cache and out/
        info = takes.probe(f)
        a, b = takes.speech_bounds(f, info["duration"])
        speech = b - a
        flags = []
        if not info["has_audio"]:
            flags.append("NO SOUND")
        if info["height"] > info["width"]:
            flags.append("portrait (will be cropped to the face)")
        if speech < est * 0.6:
            flags.append(f"short: {speech:.0f} s of speech vs ~{est:.0f} s expected (missing lines?)")
        if speech > est * 1.6 + 5:
            flags.append(f"long: {speech:.0f} s vs ~{est:.0f} s expected (extra takes in one file?)")
        rows.append((tag, "ok" if not flags else "CHECK", f"{speech:.0f} s of speech; " + "; ".join(flags) if flags else f"{speech:.0f} s of speech"))
    width = max(len(r[0]) for r in rows)
    for r in rows:
        print(f"{r[0]:<{width}}  {r[1]:<8} {r[2]}")
    print()
    if missing:
        print("Missing by person:")
        for who, tags in missing.items():
            print(f"  {who}: {', '.join(tags)}")
    else:
        print("All takes are in.")


if __name__ == "__main__":
    main()
