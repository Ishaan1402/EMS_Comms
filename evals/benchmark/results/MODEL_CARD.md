# Model card: Eir AI triage suggestion

## What it is
- Model **gpt-6-luna** (OpenAI), reasoning effort `none`, with the app's prompt
  `awareness_v4` (sha256 `22bb30c65e99e6d6…`), called by `backend.case_assessment`
  at app commit `d7c6a51`.
- Reads the EMS crew's report (radio transcript and typed notes) and the earlier report the hospital acknowledged.
  Returns a suggested ESI level (1-5, or Cannot assess), its reason, a risk score (1-10) and a handoff summary.

## Intended use
- A suggestion shown to emergency department staff beside each incoming patient. Clinicians can change the ESI level.
- The hospital's Standard queue order ignores it; it orders the queue only when a hospital turns on AI-assisted order.
- "Cannot assess" is shown as Needs review, never as a level. Numbers the reports don't state are flagged.
- Not for: triage without a clinician, diagnosis, or any use without the crew's report.

## Evaluation data (benchmark run 2026-10-26)
- KTAS (Moon et al. 2019, PLoS ONE, CC BY 4.0): 1,267 real ED visits, expert KTAS as the reference.
- MIMIC-IV-ED Demo v2.2 (PhysioNet, ODbL): 207 stays from 56 patients, triage nurse ESI as the reference.
- 10 fictional cases labelled by one clinician (Purav).
- Reports were written from triage fields: shorter and cleaner than real radio, no speech-to-text step.

## Results (primary run)
- Of 154 KTAS ambulance arrivals experts rated 1-2: Eir put 138 at 1-2,
  held 14 for review, placed
  2 at 3-5. Triage nurses placed
  15 at 3-5.
- Exact level agreement is modest: KTAS ambulance 139/277 (50%), within one level
  250/277 (90%), weighted kappa 0.34
  (0.25-0.43). It leans urgent: over-triage 130/277 (47%).
- On MIMIC its exact agreement is no better than always guessing ESI 2.
- Same input three times: identical ESI for 202/235 (86%) of KTAS ambulance cases.
- Handoff number check caught 23,684/23,684 (100%) of injected wrong numbers the reports did not contain.
- Median 3.1 s per assessment; about $0.0002 per call at list price.

## Baseline: a SAS model on the numbers alone
SAS gradient boosting (100 trees, CAS `decisionTree.gbtreeTrain`) on age, sex, arrival, mental state and six vitals,
trained on 70% of the KTAS visits and compared on the same 30% holdout; the matched cutoff is tuned on that holdout, which favours the model (`Public.EIR_BENCH_FEATURES`, `_PartInd_ = 1`).

| Who | Reads | Urgent caught | Held for review | Urgent missed | Non-urgent flagged |
| --- | --- | --- | --- | --- | --- |
| Eir AI | the crew's report | 63 | 9 | 2 | 77 of 306 |
| SAS gradient boosting, default cutoff | age, sex, arrival, mental state, 6 vitals | 32 | 0 | 42 | 13 of 306 |
| SAS gradient boosting, cutoff tuned on the holdout to Eir's false flags | age, sex, arrival, mental state, 6 vitals | 53 | 0 | 21 | 77 of 306 |
| Triage nurses | the patient, at triage | 62 | 0 | 12 | 11 of 306 |
| Simple vitals rule | mental state and 5 vitals | 33 | 0 | 41 | 71 of 306 |

## Patient outcomes (KTAS, what happened after triage)
A triage level is a judgement, even the experts'. The KTAS source also records each visit's disposition:

| Patients who | Visits | Eir: 1-2 / no level / 3-5 | Triage nurses: 1-2 / 3-5 | KTAS experts: 1-2 / 3-5 |
| --- | --- | --- | --- | --- |
| ICU, death or surgery from ED | 39 | 22 / 13 / 4 | 20 / 19 | 20 / 19 |
| Ward admission | 373 | 195 / 136 / 42 | 118 / 255 | 125 / 248 |
| Sent home (discharge or AMA) | 823 | 255 / 439 / 129 | 78 / 745 | 83 / 740 |

## Fairness (SAS `fairAITools.assessBias` and Fisher's exact test)
Urgent visits put at 3-5, by group:
- KTAS by sex, put at 3-5: Female 5/100, Male 2/146 (Fisher p 0.124)
- KTAS by sex, no level: Female 11/100, Male 20/146 (Fisher p 0.564)
- KTAS by age band, put at 3-5: 65 and over 1/124, Under 65 6/122 (Fisher p 0.065)
- KTAS by age band, no level: 65 and over 8/124, Under 65 23/122 (Fisher p 0.004, significant after 8 tests)
- MIMIC by sex, put at 3-5: Female 5/59, Male 7/56 (Fisher p 0.551)
- MIMIC by sex, no level: Female 16/59, Male 13/56 (Fisher p 0.672)
- MIMIC by age band, put at 3-5: 65 and over 8/50, Under 65 4/65 (Fisher p 0.124)
- MIMIC by age band, no level: 65 and over 16/50, Under 65 13/65 (Fisher p 0.194)

KTAS urgent patients got no level 65 and over 8/124 vs Under 65 23/122 (Fisher p 0.004); no other gap was significant in 8 tests. Eight two-sided Fisher tests (2 datasets x 2 variables
x 2 outcomes), significant below 0.05 / 8. A visit with no level goes to Needs review, so a clinician sees it. The counts
are small; these are the checks to repeat on live data.

## Limits
- No result is clinical validation. KTAS is not ESI; use it for "urgent flagged urgent", not ESI accuracy.
- The MIMIC intervals treat repeat visits as independent. Reasons were not graded. Transcription was not tested.

Source: `evals/benchmark/results/REPORT.md` and `metrics.json` in the Eir repository.
