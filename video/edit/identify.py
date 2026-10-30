"""Name unnamed takes ("Movie on 10-29-26 at 10.48 PM.mov") by what's said in them.

    python identify.py [files...]      # default: every unnamed video in ~/Downloads newer than Oct 29

Transcribes each take (Whisper, cached), matches the words against every expected line in the script,
and copies it into recordings/ as <video>-<NN>-<name>.<ext> when the match is clear. The original is
left where it is. Prints what it did and anything it couldn't place.
"""
import json
import re
import shutil
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

import intake
import takes

DOWNLOADS = Path.home() / "Downloads"


def candidates():
    for f in sorted(DOWNLOADS.iterdir()):
        if f.suffix.lower() in (".mov", ".mp4", ".m4v") and not re.match(r"^(jury|pitch)", f.stem.lower()) \
                and f.stat().st_mtime > 1793246400 and "roughcut" not in f.stem.lower():  # after Oct 29 2026 local
            yield f


def norm(text):
    return " ".join(takes.norm(w) for w in text.split() if takes.norm(w))


def main(files):
    expected = [(tag, who, text) for tag, who, text, _ in intake.expected()]
    takes.REC.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    manifest = takes.REC / ".imported.json"
    done = json.loads(manifest.read_text()) if manifest.exists() else {}
    for f in files:
        key = f"{f.resolve()}|{f.stat().st_size}"
        if key in done:
            continue
        if not takes.probe(f)["has_audio"]:
            continue  # not a take (no sound)
        _, meta = takes.prepare(f, tmp)
        said = norm(" ".join(w[0] for w in meta["words"] or []))
        if not said:
            print(f"? {f.name}: no speech found")
            continue
        scored = sorted(((SequenceMatcher(None, said, norm(text)).ratio(), tag) for tag, _, text in expected if text.strip()), reverse=True)
        (best, tag), (second, _) = scored[0], scored[1]
        if best < 0.45 or best - second < 0.08:
            print(f"? {f.name}: unclear ({tag} {best:.2f}, next {second:.2f}); says: {said[:90]}")
            continue
        dest = takes.REC / (tag + f.suffix.lower())
        if dest.exists():
            dest = takes.REC / f"{tag}-take{len(list(takes.REC.glob(tag + '*'))) + 1}{f.suffix.lower()}"
            print(f"! {f.name}: {tag} already has a take; kept both, new one is {dest.name} (tell me which to use)")
        shutil.copy2(f, dest)
        dest.chmod(0o444)
        done[key] = dest.name
        manifest.write_text(json.dumps(done, indent=1))
        print(f"= {f.name} -> {dest.name}  (match {best:.2f})")


if __name__ == "__main__":
    main([Path(a) for a in sys.argv[1:]] or list(candidates()))
