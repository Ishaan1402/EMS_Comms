# Building the Eir videos (no Claude needed)

Everything here runs on Ishaan's Mac from `~/EMS_Comms/video/edit`. Any terminal, the Codex app, or a teammate can do it.

## 0. Once
```
cd ~/EMS_Comms/video/edit
python3 -m venv .venv && .venv/bin/pip install pillow openai python-dotenv   # skip if .venv exists
```
`EIR_WHISPER=1` uses the OpenAI key in `~/EMS_Comms/.env` for word timings (cues land on the exact phrase). Without it, cues are estimated.

## 1. Bring in the clips
Put each person's clips in `recordings/` named `jury-08-mayank.mp4`, `pitch-04-anand.mov`, ... (names in the parts doc), or leave them anywhere in ~/Downloads with any name and run:
```
EIR_WHISPER=1 .venv/bin/python identify.py     # names unnamed clips by what's said; originals untouched
.venv/bin/python intake.py                      # who's missing, too short, silent, portrait
```
Raw clips are made read-only; the editor only writes to `recordings/.cache/` and `out/`.

## 2. Build
```
EIR_WHISPER=1 .venv/bin/python build.py jury     # -> out/InTheBud-Jury.mp4
EIR_WHISPER=1 .venv/bin/python build.py pitch    # -> out/InTheBud-Pitch.mp4
EIR_WHISPER=1 .venv/bin/python build.py jury --only 8   # one slide, to check it
```
It prints each slide's length and the total. Jury must be ≤ 10:00, pitch ≤ 3:00, file < 500 MB.
Missing clips get a computer voice and a grey box: that's how you spot gaps.
`EIR_CAM=cutout` puts people in as cut-outs instead of the framed corner window.

## 3. Trim without re-recording
- Cut a sentence from a take: add it to `drops.json`, e.g. `{"jury-16-vrishank": ["The safety check caught every planted wrong number"]}` (the first few words are enough), then rebuild.
- Drop a whole slide: `skip.json`, e.g. `{"jury": [11]}`.

## 4. Change what's on screen
- Slides: replace `slides/jury/NN.png` or `slides/pitch/NN.png` (1920x1080, keep the bottom-right corner empty for the camera).
- Which SAS clip plays when, and on which phrase: the `JURY` / `PITCH` lists at the top of `build.py` (`clip(file, start_s, end_s, at="phrase")`).
- Script text the editor uses for timing: `script.json` (keep it in step with the Google Doc).
- Diagrams: `diagram/*.html`, render with `NODE_PATH=~/.npm/_npx/e41f203b7505f1fb/node_modules node diagram/render.js file.html out.png`.

## 5. If something breaks
The error names the ffmpeg command that failed. Most common: a clip with no sound (re-export it) or a file name that doesn't match (run `intake.py`).
