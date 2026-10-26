# Eir AI benchmark: results (run of 2026-10-26)

Every number below is in [metrics.json](metrics.json), recomputed from the raw call records in
[raw/](raw/) by `python -m evals.benchmark.score`; the full tables are in [TABLES.md](TABLES.md).
The analysis plan was committed before any model call ([PROTOCOL.md](../PROTOCOL.md), commit
`d7c6a51`). Sources and licences: [SOURCES.md](SOURCES.md).

## What ran

- The app's own code at commit `d7c6a51`: assessment prompt `awareness_v4` (sha256 `22bb30c6…`),
  extraction prompt `extraction_v3` (sha256 `d41d971a…`), model **gpt-6-luna**, reasoning effort
  `none`, through `backend.case_assessment` and `backend.case_extraction`.
- 3,201 assessment calls and 1,895 extraction calls on 2026-10-26 between 06:04 and 06:28 UTC,
  on Ishaan's Mac. **0 errors, 0 unreadable answers.** List-price cost $1.09.
- 1,474 real, de-identified ED patients (1,267 KTAS, 207 MIMIC-IV-ED Demo) and Purav's 10
  fictional cases. Each real patient's report was written from triage fields only.

## Headline results

### Critical and emergent patients (the safety question)

Reference level 1-2 patients, all of them, primary run:

| Set | Reference 1-2 | AI gave 1-2 | AI gave 3-5 | AI gave no level (Cannot assess, shown as Needs review) |
|---|---|---|---|---|
| KTAS ambulance arrivals (expert KTAS) | 154 | 138 (90%) | 2 (1%) | 14 (9%) |
| KTAS, same patients, numbers spoken as words | 154 | 141 (92%) | 5 (3%) | 8 (5%) |
| KTAS, all patients | 246 | 208 (85%) | 7 (3%) | 31 (13%) |
| MIMIC ambulance/helicopter (nurse ESI) | 75 | 48 (64%) | 10 (13%) | 17 (23%) |
| Purav's 10 (one clinician's ESI) | 6 | 6 | 0 | 0 |

Every reference level 1 patient that got a level was put at 1-2: KTAS ambulance 16/16, KTAS all
19/19, MIMIC 11/11, Purav 2/2.

For comparison, on the same 154 KTAS ambulance patients the triage nurses' own KTAS put **15
(10%)** at 3 or lower. The nurses used KTAS itself, so this is a same-scale human yardstick; the
AI was asked for ESI, a different 5-level scale, so the comparison is coarse (urgent vs not).

### Exact level agreement (where Eir is weaker)

| Set | AI gave a level | Exact | Within one | Weighted kappa (quadratic, 95% CI) | Under-triage | Over-triage |
|---|---|---|---|---|---|---|
| KTAS ambulance | 277/421 | 139/277 (50%) | 250/277 (90%) | 0.34 (0.25-0.43) | 8/277 (3%) | 130/277 (47%) |
| KTAS all | 668/1,267 | 293/668 (44%) | 584/668 (87%) | 0.40 (0.34-0.45) | 17/668 (3%) | 358/668 (54%) |
| MIMIC ambulance | 90/119 | 49/90 (54%) | 90/90 (100%) | 0.12 (-0.06-0.29) | 15/90 (17%) | 26/90 (29%) |
| MIMIC all | 153/207 | 74/153 (48%) | 150/153 (98%) | 0.16 (0.04-0.28) | 25/153 (16%) | 54/153 (35%) |
| Purav's 10 | 10/10 | 5/10 (50%) | 10/10 (100%) | 0.53 (0.25-0.81; bootstrap -0.00-0.76) | 2/10 | 3/10 |

Comparators: KTAS nurses vs experts, ambulance: exact 352/421 (84%), kappa 0.87. A simple
vitals-and-consciousness rule (ours): KTAS ambulance exact 194/421 (46%), kappa 0.31, and only
84/154 (55%) of level 1-2 patients at 1-2; MIMIC ambulance 33/75 (44%). Always guessing the most
common level: KTAS ambulance 169/421 (40%), MIMIC ambulance 68/119 (57%).

Read plainly: Eir leans urgent. It rarely puts a sick patient too low, but it over-triages about
half the time on KTAS, and on MIMIC its exact match with the triage nurse is **no better than
always guessing ESI 2** (54% vs 57%; kappa's interval includes 0).

### Cannot assess

On one-line triage summaries the AI declined to give a level for a large share: KTAS ambulance
144/421 (34%), KTAS all 599/1,267 (47%), MIMIC 54/207 (26%), Purav 0/10. It declined mostly on
lower-acuity reports (KTAS ambulance: 130 of 267 reference 3-5 vs 14 of 154 reference 1-2) and
said what was missing (for example "abdominal pain onset and associated symptoms"). In the app
this shows as Needs review, not as a level. Real radio reports are longer than these summaries,
so this rate says little about live use either way; Purav's fuller cases had none.

### Consistency

- Same input, repeated calls: identical ESI in all 3 runs for 202/235 KTAS ambulance cases (86%)
  and 130/140 MIMIC cases (93%); within one level in all runs for 100% of both. Purav's 10 over 5
  runs: identical 7/10, within one 9/10; exact agreement with Purav ranged 3/10 to 5/10 by run.
- Numbers in digits vs spoken as words, same 421 patients (252 got a level both ways): identical
  ESI 226/252 (90%), within one 252/252.

### Guardrails

- **Handoff number check**, 25,500 injected wrong numbers in 1,905 real handoffs: it caught
  **23,684/23,684 (100%)** whose wrong value the reports did not contain. 1,816 (7.1%) wrong values
  happened to equal another number in the report (or its °F/°C conversion) and can't be caught by
  this design. An invented reading appended to each handoff: caught 1,905/1,905.
- **On the unmodified outputs**, the check flagged 9 of 3,201 (6 of 1,905 primary runs). All 9
  were the same false alarm: the ESI reason quoting the prompt's "over age 12" threshold, which
  the check's list of allowed ESI thresholds lacks. Apart from those, the check found **no number
  the model made up**. (Fix, not made here: add 12 to `ESI_THRESHOLDS` in backend/case_assessment.py.)
- **Extraction quote check**, 9,053 real extracted vitals: a wrong value with its true quote was
  dropped 9,053/9,053; a quote not in the report failed the run 9,053/9,053.

### Vital sign extraction

10,232 of 10,233 stated vital signs extracted with the right value, **0 wrong values** (KTAS
6,790/6,791, MIMIC 1,187/1,187, spoken words 2,255/2,255). The one miss: a temperature in a
report where two other vitals "could not be measured". The reports are clean summaries, so this
measures reading stated numbers, not hearing them over a noisy radio.

### Speed and cost (these calls, on a home connection, 8 at a time)

Assessment: median 3.1 s, p90 5.0 s, about 1,316 tokens in and 182 out, $0.0002 per call at list
price. Extraction: median 2.5 s, p90 3.2 s, $0.0002 per call.

## Checking it in SAS

Upload [esi_pairs.csv](esi_pairs.csv) and run `sas/eir_ai_agreement.sas`. PROC FREQ's weighted
kappa (Fleiss-Cohen) should print: purav/all 0.53, mimic/all 0.16, mimic/ems 0.12, ktas/all 0.40,
ktas/ems 0.34, ktas_spoken/all 0.32, and 0.88 for KTAS nurses vs experts. Not yet run in SAS; the
Python formulas match statsmodels' implementation of the same standard error.

## Limits

- **No result here is clinical validation.** The references are one triage nurse (MIMIC), expert
  retrospective review on a different scale (KTAS), and one clinician on fictional cases (Purav).
- KTAS is not ESI. Use KTAS results for "did urgent patients get flagged urgent", not ESI accuracy.
- MIMIC-IV-ED Demo stays are mostly admitted patients (ESI 2-3; only 2 ESI 4 and no ESI 5).
- The reports are generated from triage fields, shorter and cleaner than real radio, with no
  speech-to-text step. Transcription was not tested.
- ESI reasons sometimes misstate the rule they cite (e.g. KTAS-0245: "pulse of 119 is below the
  over-100 threshold"). Reasons were not graded.
- Purav's set is 10 cases: one changed answer moves exact agreement by 10 points.
- Not measured: the risk score (no reference), handoff quality, queue order, outcomes.

## Changes after the run (none to the model calls)

- `guardrails.py`: collisions now also count a wrong temperature that equals a °F/°C conversion of
  a stated number, as the check itself does. Before this, 65 such mutants were miscounted as missed.
- `score.py`/`report.py`: added "AI gave a level" and the all-cases reference 1-2 breakdown above.
- `run.log` from the Mac was not committed (the repo ignores `*.log`); `raw/runs.jsonl` records
  the same run settings. The second `runs.jsonl` entry shows `git_dirty: true` only because the
  smoke test's untracked result files existed; the code was `d7c6a51` unchanged.

## Slide-ready lines (jury slide 11)

Each line is exact; footnote the source set on the slide.

1. "We tested Eir's AI on 1,474 real, de-identified emergency patients from two public datasets,
   plus our clinician-labelled cases: 5,096 AI calls, every one logged."
2. "Of 154 ambulance patients experts rated critical or emergent, Eir put 138 in its top two
   levels, held 14 for clinician review, and placed 2 lower." (KTAS data, Moon et al. 2019)
3. "Triage nurses in the same study placed 15 of those 154 lower." (same-scale human yardstick;
   say "for context", not "Eir beats nurses")
4. "We planted 25,500 wrong numbers in real AI handoffs. The check caught every one that wasn't
   already in the report: 23,684 of 23,684." (the other 1,816 equalled another number in the report)
5. "It read 10,232 of 10,233 vital signs correctly, and never recorded a wrong value."
6. "About 3 seconds and two hundredths of a cent per assessment."
7. "Where it falls short: Eir leans urgent. Exact level matches were about half, which is why the
   clinician always makes the call."

Do not say "accuracy", "validated", "outperforms nurses", or quote Purav's 10 as a percentage.
