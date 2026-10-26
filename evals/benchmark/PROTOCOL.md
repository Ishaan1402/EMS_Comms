# Eir AI benchmark: protocol (fixed before the run)

Written and committed **before any model call of this benchmark**, so the analysis cannot be fitted to
the results. Any change after the run gets a new protocol version and a new run; the first run's
results stay in the repository either way.

Protocol version: `eir-benchmark-v1` (2026-10-26).

## What is tested

The app's own AI steps, called through the same functions the server uses, with the model set in
the app's `.env` (recorded in `results/raw/runs.jsonl`):

1. **ESI suggestion**: `backend.case_assessment.score -> interpret -> check_numbers` (prompt
   `awareness_v4`). The suggested ESI is compared with a reference label.
2. **Handoff number check** (guardrail): `unsupported_numbers`, on unmodified outputs and on
   mutated copies with wrong numbers injected.
3. **Vital sign extraction**: `backend.case_extraction.extract -> interpret` (prompt
   `extraction_v3`), including its quote check, against the values the report states.

Not tested: speech-to-text, the queue order, the risk score's own accuracy (no reference exists),
handoff quality, or anything clinical beyond agreement with the labels below.

## Datasets and references

| Set | Cases | Real patients? | Reference label | Licence |
|---|---|---|---|---|
| `purav` | 10 | No, fictional | One clinician's ESI (Purav, patient review v2.1) | Team's own |
| `mimic` | 207 stays with an ESI (119 ambulance/helicopter) | Yes, de-identified | ESI the triage nurse assigned at the time | ODbL 1.0 (MIMIC-IV-ED Demo v2.2) |
| `ktas` | 1,267 (421 ambulance: public 119 + private) | Yes, de-identified | KTAS by three KTAS-instructor experts on retrospective review (Moon et al. 2019) | CC BY 4.0 |
| `ktas_spoken` | the 421 ambulance cases | as above | as above | as above |

Reports for `mimic` and `ktas` are written by `build_cases.py` from triage fields only (age, sex,
chief complaint, vitals, pain score, consciousness for KTAS, injury flag for KTAS). No diagnosis,
disposition or later information is used. `ktas_spoken` is the same report with numbers said as
words ("pulse one oh five"), as on the radio.

KTAS is a different 5-level scale from ESI (both: 1 most urgent). The AI is asked for ESI, as in the
app. KTAS agreement is therefore an external acuity check, not ESI accuracy; the most scale-robust
metric is whether level 1-2 patients get a 1-2.

## Plan

- Assessment: `purav` x5 repeats, `mimic` x3, `ktas` x1 (ambulance subset x3), `ktas_spoken` x1.
- Extraction: `mimic`, `ktas`, `ktas_spoken`, x1 each.
- **Repeat 1 is the primary result for every case.** Other repeats measure consistency only.
- Concurrency 8; a failed call is retried by rerunning; every attempt is kept.

## Primary metrics (on `mimic/ems` and `ktas/ems`)

1. Exact agreement and agreement within one level, with 95% Wilson intervals.
2. Quadratic weighted kappa (Fleiss-Cohen weights over levels 1-5, as SAS `AGREE(WT=FC)`), with
   SAS's asymptotic 95% interval and a case bootstrap interval (2,000 resamples, seed 20261026).
3. Under-triage rate (AI less urgent than the reference) and **high-acuity sensitivity**: of the
   reference level 1-2 patients, how many the AI put at 1-2.
4. Guardrail catch rate on injected wrong numbers.

Secondary: the all-patient sets, `purav`, linear and unweighted kappa, consistency across repeats,
digits vs spoken words, extraction precision and recall, flag rate on unmodified outputs,
latency, tokens and list-price cost, and two comparisons: the triage nurses' own KTAS against the
experts (KTAS data only, same scale, so the fairer human yardstick is on KTAS, not ESI) and a
simple vitals rule (`score.rule_baseline`, ours, not a clinical tool).

## Exclusions and counting

- A case with no reference label is not built (15 MIMIC stays have no ESI).
- A failed call, an unreadable answer or "Cannot assess" has no ESI: each is counted and reported,
  and the agreement metrics use only cases with an ESI (denominators are always shown).
- Vital values outside the app's plausible ranges are left out of the report, as are free-text
  pain entries in MIMIC ("unable", "Critical") and pain scores above 10.

## Stated limits (true whatever the results)

- The MIMIC reference is one triage nurse's real-time ESI, not an adjudicated truth. MIMIC Demo
  stays are mostly admitted patients, so ESI 4-5 are almost absent.
- KTAS is not ESI. The KTAS reference is expert consensus, but on retrospective record review.
- Reports built from triage fields are cleaner and shorter than real radio traffic.
- `purav` is 10 fictional cases and one rater: it can show coarse agreement only.
- No result here is clinical validation, and none supports autonomous triage.
