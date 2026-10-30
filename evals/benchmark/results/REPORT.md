# Eir AI benchmark: results (run of 2026-10-26)

Every number below is in [metrics.json](metrics.json) (patient outcomes in [outcomes.json](outcomes.json)), recomputed from the raw call records in
[raw/](raw/) by `python -m evals.benchmark.score`; the full tables are in [TABLES.md](TABLES.md).
The analysis plan was committed before any model call ([PROTOCOL.md](../PROTOCOL.md), commit
`d7c6a51`). Sources and licences: [SOURCES.md](SOURCES.md).

## What ran

- The app's own code at commit `d7c6a51`: assessment prompt `awareness_v4` (sha256 `22bb30c6…`),
  extraction prompt `extraction_v3` (sha256 `d41d971a…`), model **gpt-6-luna**, reasoning effort
  `none`, through `backend.case_assessment` and `backend.case_extraction`.
- 3,201 assessment calls and 1,895 extraction calls on 2026-10-26 between 06:04 and 06:38 UTC (assessments until 06:28, then extractions),
  on Ishaan's Mac. **0 errors, 0 unreadable answers.** List-price cost $1.09.
- 1,474 real, de-identified ED visits (1,267 KTAS records; 207 MIMIC-IV-ED Demo visits from 56
  patients) and Purav's 10 fictional cases. Each visit's report was written from triage fields only.

## Headline results

### Critical and emergent visits (the safety question)

Every visit with reference level 1-2, primary run:

| Set | Reference 1-2 | AI gave 1-2 | AI gave 3-5 | AI gave no level (Cannot assess, shown as Needs review) |
|---|---|---|---|---|
| KTAS ambulance arrivals (expert KTAS) | 154 | 138 (90%) | 2 (1%) | 14 (9%) |
| KTAS, same visits, numbers spoken as words | 154 | 141 (92%) | 5 (3%) | 8 (5%) |
| KTAS, all visits | 246 | 208 (85%) | 7 (3%) | 31 (13%) |
| MIMIC ambulance/helicopter (nurse ESI) | 75 | 48 (64%) | 10 (13%) | 17 (23%) |
| Purav's 10 (one clinician's ESI) | 6 | 6 | 0 | 0 |

Every reference level 1 visit that got a level was put at 1-2: KTAS ambulance 16/16, KTAS all
19/19, MIMIC 11/11, Purav 2/2.

For context only, on the same 154 KTAS ambulance visits the triage nurses' own KTAS put **15
(10%)** at 3 or lower. This is not a fair head-to-head: the experts' label and the AI's report both
come from the same written record, while the nurses triaged the live patient; the AI could also
decline to give a level and the nurses could not; and the AI was asked for ESI, a different scale.
Do not present it as Eir doing better than nurses.

### Exact level agreement (where Eir is weaker)

| Set | AI gave a level | Exact | Within one | Weighted kappa (quadratic, 95% CI) | Under-triage | Over-triage |
|---|---|---|---|---|---|---|
| KTAS ambulance | 277/421 | 139/277 (50%) | 250/277 (90%) | 0.34 (0.25-0.43) | 8/277 (3%) | 130/277 (47%) |
| KTAS all | 668/1,267 | 293/668 (44%) | 584/668 (87%) | 0.40 (0.34-0.45) | 17/668 (3%) | 358/668 (54%) |
| MIMIC ambulance | 90/119 | 49/90 (54%) | 90/90 (100%) | 0.12 (-0.06-0.29) | 15/90 (17%) | 26/90 (29%) |
| MIMIC all | 153/207 | 74/153 (48%) | 150/153 (98%) | 0.16 (0.04-0.28) | 25/153 (16%) | 54/153 (35%) |
| Purav's 10 | 10/10 | 5/10 (50%) | 10/10 (100%) | 0.53 (0.25-0.81; bootstrap -0.00-0.76) | 2/10 | 3/10 |

Comparators on the same visits the AI gave a level for (same denominators as the table):

| Set | AI exact | Always the most common level | Triage nurses (KTAS) vs experts |
|---|---|---|---|
| KTAS ambulance | 139/277 (50%) | always 3: 99/277 (36%) | 231/277 (83%), kappa 0.86 |
| KTAS all | 293/668 (44%) | always 3: 275/668 (41%) | 563/668 (84%), kappa 0.87 |
| MIMIC ambulance | 49/90 (54%) | always 2: 54/90 (60%) | n/a |
| MIMIC all | 74/153 (48%) | always 2: 75/153 (49%) | n/a |

A simple vitals-and-consciousness rule (ours, `score.rule_baseline`) put only 84/154 (55%) of the
KTAS ambulance level 1-2 visits at 1-2, and 33/75 (44%) on MIMIC ambulance (same denominators as
the urgent-visits table above).

Read plainly: Eir leans urgent. On KTAS it rarely put an urgent visit too low (2 of 154 on
ambulance arrivals), but it over-triages about half the time. On MIMIC it placed 10 of 75 urgent
ambulance visits at 3-5, and its exact match with the triage nurse is **no better than always
guessing ESI 2** (49/90 vs 54/90; kappa's interval includes 0).

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
- Numbers in digits vs spoken as words, same 421 visits (252 got a level both ways): identical
  ESI 226/252 (90%), within one 252/252.

### Guardrails

- **Handoff number check**, 25,500 injected wrong numbers in 1,905 real handoffs: it caught
  **23,684/23,684 (100%)** whose wrong value the reports did not contain. 1,816 (7.1%) wrong values
  happened to equal another number in the report (or its °F/°C conversion) and can't be caught by
  this design. An invented reading appended to each handoff: caught 1,905/1,905.
- **On the unmodified outputs**, the check flagged 9 of 3,201 calls (6 of 1,905 primary runs; both in metrics.json). All 9
  were the same false alarm: the ESI reason quoting the prompt's "over age 12" threshold, which
  the check's list of allowed ESI thresholds lacks. Apart from those, the check found **no number
  the model made up**. (The benchmark ran on the earlier code; the fix, adding the age-12 and 28-day thresholds to backend/case_assessment.py, was merged afterwards in PR #85.)
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

## Added 2026-10-27: NHAMCS 2022, US ambulance arrivals (protocol addendum v1.1)

Plan committed in `e1f21a1` before any call on this set; run on `4c92b43` (clean), 1,683 assessment
calls, **0 errors**, $0.38, median 2.6 s. Every number is in `metrics.json` (`esi.nhamcs/all`,
`added_sets.nhamcs`) and `outcomes.json`; the v1 figures above are unchanged. The set: CDC/NCHS
NHAMCS 2022 ED file, adult ambulance arrivals from the scene with a triage level, 1,683 visits from
129 hospitals. Reports are built from coded reasons for visit, vitals and pain only. Counts are of
the sample, not national estimates (no survey weights).

**What happened to the patient** (the outcome check; outcomes kept apart from the report):

| Patients who | Visits | Eir: 1-2 / no level / 3-5 | ED triage nurse: 1-2 / 3-5 | Simple vitals rule: 1-2 / 3-5 |
|---|---|---|---|---|
| went to ICU, the OR or the cath lab, or died | 155 | 110 / 38 / 7 | 105 / 50 | 85 / 70 |
| were admitted to a ward | 351 | 224 / 95 / 32 | 155 / 196 | 142 / 209 |
| were sent home (incl. AMA) | 1,073 | 597 / 393 / 83 | 237 / 836 | 280 / 793 |

Read plainly: of the critical-outcome patients, Eir put slightly more at 1-2 than the ED's own triage
did (110 vs 105) and put far fewer at 3-5 (7 vs 50), but held 38 for review; counted as "not flagged
urgent", that is 45 for Eir and 50 for the ED. It over-flags heavily: 56% of patients sent home got
1-2 (ED: 22%).

**Against the ED's triage level** (one nurse at the time, each hospital's scale, usually ESI):

| Metric | Result |
|---|---|
| Triaged 1-2: Eir at 1-2 / no level / 3-5 | 366 / 139 / 27 of 532 (69% / 26% / 5%) |
| Triaged 1, given a level: put at 1-2 | 51 of 53 |
| Gave a level | 1,115 of 1,683 (66%) |
| Exact / within one level | 396 (36%) / 1,014 (91%) of 1,115 |
| Always guessing level 3, same visits | 606 of 1,115 (54%): better than Eir's exact match |
| AI less / more urgent than the nurse | 76 (7%) / 643 (58%) of 1,115 |
| Weighted kappa (quadratic) | 0.10 (0.06-0.14) |

Exact agreement with one nurse's level is weak, as on MIMIC; the outcome table is the stronger evidence.

**Fairness** (urgent = triaged 1-2; 10 Fisher tests, Bonferroni bar p < 0.005): no gap is
significant. Put at 3-5: by sex p 0.84, by age band p 0.84, each race/ethnicity group against
non-Hispanic White p 0.50 to 1.0. No level: men 89/295 vs women 50/237 (p 0.022) and under 65
91/309 vs 65 and over 48/223 (p 0.046), both above the bar. The under-65 direction matches KTAS's
significant gap (23/122 vs 8/124).

**Number check** on the unmodified outputs: 3 of 1,683 flagged, all the known "over age 12" false
alarm; 21,914 of 21,914 injected wrong numbers not in the report were caught.

Limits specific to this set: reports from coded reasons are terser than KTAS's free-text complaints
(and there is no mental status), which likely raises "Cannot assess" (34% here); 2022 includes
COVID-19-era visits; the triage scale may differ by hospital.

## Checking it in SAS

`python -m evals.benchmark.to_sas push` loads these results into CAS (`Public.EIR_BENCH_CALLS`), and
`sas/eir_ai_agreement.sas` reads that table in SAS Studio (or upload [esi_pairs.csv](esi_pairs.csv)). PROC FREQ's weighted
kappa (Fleiss-Cohen) should print: purav/all 0.53, mimic/all 0.16, mimic/ems 0.12, ktas/all 0.40,
ktas/ems 0.34, ktas_spoken/all 0.32, and 0.88 for KTAS nurses vs experts. The linear
(Cicchetti-Allison) kappas should print: 0.36, 0.08, 0.04, 0.31, 0.27, 0.25, and 0.83 for the nurses. Not yet run in SAS; the
Python formulas match statsmodels' implementation of the same standard error.

## Limits

- **No result here is clinical validation.** The references are one triage nurse (MIMIC), expert
  retrospective review on a different scale (KTAS), and one clinician on fictional cases (Purav).
- KTAS is not ESI. Use KTAS results for "did urgent patients get flagged urgent", not ESI accuracy.
- MIMIC-IV-ED Demo visits are mostly admitted patients (ESI 2-3; only 2 ESI 4 and no ESI 5).
- The 207 MIMIC visits come from 56 patients (the 119 ambulance visits from 38). The intervals
  treat visits as independent, so the MIMIC intervals are too narrow. KTAS records are counted as
  visits too; the source does not say whether a patient appears twice.
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

1. "We tested Eir's AI on 1,474 real, de-identified emergency department visits from two public
   datasets, plus our clinician-labelled cases: 5,096 AI calls, every one logged."
2. "Of 154 ambulance arrivals experts rated critical or emergent, Eir put 138 in its top two
   levels, held 14 for clinician review, and placed 2 lower. On a second dataset, it placed 10 of
   75 urgent ambulance visits lower." (KTAS, Moon et al. 2019; MIMIC-IV-ED Demo, nurse ESI). Never
   use the first sentence without the second.
3. (Dropped: the triage nurse comparison. The experts and the AI read the same record while the
   nurses saw the live patient, and the AI could abstain, so on a slide it reads as "beats nurses".)
4. "We planted 25,500 wrong numbers in real AI handoffs. The check caught every one that wasn't
   already in the report: 23,684 of 23,684." (the other 1,816 equalled another number in the report)
5. "It read 10,232 of 10,233 vital signs correctly, and never recorded a wrong value."
6. "About 3 seconds and two hundredths of a cent per assessment."
7. "Where it falls short: Eir leans urgent. Exact level matches were about half, which is why the
   clinician always makes the call."

Do not say "accuracy", "validated", "outperforms nurses", or quote Purav's 10 as a percentage.
