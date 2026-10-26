# Eir AI benchmark

Measures the app's own AI steps (ESI suggestion, handoff number check, vital sign extraction) on
real, public, labelled triage data and on Purav's 10 cases. The analysis plan was fixed first in
[PROTOCOL.md](PROTOCOL.md); results are in [results/](results/) and summarised in
[results/REPORT.md](results/REPORT.md).

## Reproduce

```bash
# 1. Sources (open access; checksums are checked by build_cases.py)
mkdir -p evals/local/benchmark_data && cd evals/local/benchmark_data
#   MIMIC-IV-ED Demo v2.2: https://physionet.org/content/mimic-iv-ed-demo/2.2/  -> mimic-iv-ed-demo-2.2/ed/{edstays,triage}.csv.gz
#   MIMIC-IV Demo v2.2:    https://physionet.org/content/mimic-iv-demo/2.2/     -> mimic-iv-demo-2.2/hosp/patients.csv.gz
#   KTAS (Moon et al. 2019, PLOS ONE, doi 10.1371/journal.pone.0216972), S1 Appendix:
#     https://journals.plos.org/plosone/article/file?type=supplementary&id=10.1371/journal.pone.0216972.s001
#     -> ktas/S1_EMR_review_coding_data.xlsx
cd -
# 2. Case files (already committed in cases/; this rebuilds them identically)
python -m evals.benchmark.build_cases --data evals/local/benchmark_data
# 3. The model calls (paid; needs OPENAI_API_KEY; resumable)
python -m evals.benchmark.run_live --env .env --out evals/benchmark/results/raw
# 4. Scoring (offline, deterministic)
python -m evals.benchmark.score --raw evals/benchmark/results/raw --out evals/benchmark/results
# 5. Same kappas in SAS: upload results/esi_pairs.csv, run sas/eir_ai_agreement.sas
```

Tests (offline, fake model): `python -m unittest evals.tests.test_benchmark`.

## Files

- `build_cases.py`: sources -> `cases/*.jsonl` (the app's model input, reference label, stated vitals).
- `run_live.py`: calls the backend functions; every attempt to `results/raw/*.jsonl`.
- `score.py`, `stats.py`: metrics (weighted kappa and its standard error as in SAS PROC FREQ;
  cross-checked against scikit-learn and statsmodels), Wilson intervals, bootstrap.
- `guardrails.py`: mutation tests of the two deterministic checks on the real outputs.
- `data/purav_patient_review_v2.1.csv`: Purav's returned review (source of his ESI labels).

## Data licences and credit

- MIMIC-IV-ED Demo and MIMIC-IV Demo (PhysioNet), Open Data Commons Open Database License v1.0.
  Johnson A, Bulgarelli L, Pollard T, Celi LA, Mark R, Horng S. The derived `cases/mimic.jsonl`
  is shared under the same licence.
- Moon SH, Shim JL, Park KS, Park CS. Triage accuracy and causes of mistriage using the Korean
  Triage and Acuity Scale. PLoS ONE 14(9): e0216972 (2019). CC BY 4.0. `cases/ktas*.jsonl` are
  derived from its S1 Appendix.
- MIETIC (PhysioNet) was not used: it needs credentialed access and a data use agreement.
