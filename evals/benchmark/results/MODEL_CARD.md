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
- NHAMCS 2022 (CDC/NCHS, public domain): 1,683 adult ambulance arrivals from 129 US hospitals, the ED's own
  triage level as the reference (protocol addendum v1.1, written before its model calls). Counts are of the
  sample, not national estimates.
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
- NHAMCS 2022 (US): of 155 ambulance patients who went to ICU, the OR or the cath lab, or died, Eir put 110 at 1-2, held 38 for review and put 7 at 3-5; the ED's own triage put 105 at 1-2 and 50 at 3-5. Of 1,073 sent home, Eir flagged 597/1,073 (56%) at 1-2 (ED: 237). Exact agreement with the ED's level: 396/1,115 (36%), weighted kappa 0.10.
- Same input three times: identical ESI for 202/235 (86%) of KTAS ambulance cases.
- Handoff number check caught 23,684/23,684 (100%) of injected wrong numbers the reports did not contain.
- Median 3.1 s per assessment; about $0.0002 per call at list price.

## Baseline: a SAS model on the numbers alone
SAS gradient boosting (100 trees, CAS `decisionTree.gbtreeTrain`) on age, sex, arrival, mental state and six vitals,
trained on 70% of the KTAS visits and compared on the same 30% holdout; the matched cutoff is tuned on that holdout, which favours the model (`Public.EIR_BENCH_FEATURES`, `_PartInd_ = 1`).

| Who | Reads | Urgent caught | Held for review | Urgent missed | Non-urgent flagged |
| --- | --- | --- | --- | --- | --- |
| Eir AI | the crew's report | 63 | 9 | 2 | 77 of 306 |
| Gradient boosting (default cutoff) | age, sex, arrival, mental state, 6 vitals | 32 | 0 | 42 | 13 of 306 |
| Gradient boosting (tuned on holdout) | age, sex, arrival, mental state, 6 vitals | 53 | 0 | 21 | 77 of 306 |
| Triage nurses | the patient, at triage | 62 | 0 | 12 | 11 of 306 |
| Simple vitals rule | mental state and 5 vitals | 33 | 0 | 41 | 71 of 306 |

## Patient outcomes (what happened after triage)
A triage level is a judgement, even the experts'. KTAS and NHAMCS also record each visit's disposition:

| Set | Patients who | Visits | Eir: 1-2 / no level / 3-5 | Triage nurse: 1-2 / 3-5 | KTAS experts: 1-2 / 3-5 |
| --- | --- | --- | --- | --- | --- |
| KTAS all visits | ICU, death or surgery from ED | 39 | 22 / 13 / 4 | 20 / 19 | 20 / 19 |
| KTAS all visits | Ward admission | 373 | 195 / 136 / 42 | 118 / 255 | 125 / 248 |
| KTAS all visits | Sent home (discharge or AMA) | 823 | 255 / 439 / 129 | 78 / 745 | 83 / 740 |
| NHAMCS ambulance arrivals | ICU, death or surgery from ED | 155 | 110 / 38 / 7 | 105 / 50 | n/a |
| NHAMCS ambulance arrivals | Ward admission | 351 | 224 / 95 / 32 | 155 / 196 | n/a |
| NHAMCS ambulance arrivals | Sent home (discharge or AMA) | 1073 | 597 / 393 / 83 | 237 / 836 | n/a |

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
- NHAMCS by sex, put at 3-5: Female 11/237, Male 16/295 (Fisher p 0.843)
- NHAMCS by sex, no level: Female 50/237, Male 89/295 (Fisher p 0.022)
- NHAMCS by age band, put at 3-5: 65 and over 12/223, Under 65 15/309 (Fisher p 0.843)
- NHAMCS by age band, no level: 65 and over 48/223, Under 65 91/309 (Fisher p 0.045)
- NHAMCS by race ethnicity, put at 3-5: Non-Hispanic White 13/289, Hispanic 4/59 (Fisher p 0.504)
- NHAMCS by race ethnicity, no level: Non-Hispanic White 79/289, Hispanic 11/59 (Fisher p 0.193)
- NHAMCS by race ethnicity, put at 3-5: Non-Hispanic White 13/289, Non-Hispanic Black 9/153 (Fisher p 0.502)
- NHAMCS by race ethnicity, no level: Non-Hispanic White 79/289, Non-Hispanic Black 42/153 (Fisher p 1.000)
- NHAMCS by race ethnicity, put at 3-5: Non-Hispanic White 13/289, Non-Hispanic other 1/31 (Fisher p 1.000)
- NHAMCS by race ethnicity, no level: Non-Hispanic White 79/289, Non-Hispanic other 7/31 (Fisher p 0.673)

KTAS urgent patients got no level 65 and over 8/124 vs Under 65 23/122 (Fisher p 0.004); no other gap was significant (8 tests on KTAS and MIMIC and 10 tests on NHAMCS, each family with its own Bonferroni bar). Eight two-sided Fisher tests (2 datasets x 2 variables
x 2 outcomes), significant below 0.05 / 8. A visit with no level goes to Needs review, so a clinician sees it. The counts
are small; these are the checks to repeat on live data.

## Limits
- No result is clinical validation. KTAS is not ESI; use it for "urgent flagged urgent", not ESI accuracy.
- The MIMIC intervals treat repeat visits as independent. Reasons were not graded. Transcription was not tested.

Source: `evals/benchmark/results/REPORT.md` and `metrics.json` in the Eir repository.
