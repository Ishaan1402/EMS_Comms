# Sources, licences and permitted use

Checked 2026-10-26. Files were downloaded on Ishaan's Mac on 2026-10-26 05:55 UTC; the checksums
below are verified by `build_cases.py` on every build.

| Source | Access | Licence | Files used (sha256) |
|---|---|---|---|
| MIMIC-IV-ED Demo v2.2, https://physionet.org/content/mimic-iv-ed-demo/2.2/ | Open access, no credentialing or data use agreement ("Anyone can access the files, as long as they conform to the terms of the specified license") | Open Data Commons Open Database License v1.0 (LICENSE.txt sha256 bcdfcd4f31c790e30fb645dc36414907172405897adff329ad379eed85d12017) | ed/edstays.csv.gz fc185cb1…3a5c52, ed/triage.csv.gz 086dadcb…e09b (both match PhysioNet's SHA256SUMS.txt) |
| MIMIC-IV Demo v2.2, https://physionet.org/content/mimic-iv-demo/2.2/ | Open access | ODbL v1.0 (same LICENSE.txt) | hosp/patients.csv.gz 59dcc6cd…f6fae (ages only) |
| Moon SH et al. 2019, PLOS ONE 14(9): e0216972, doi 10.1371/journal.pone.0216972, S1 Appendix | Public, part of the article ("All relevant data are within the paper and its Supporting Information files") | Creative Commons Attribution 4.0 | S1_EMR_review_coding_data.xlsx f631c951…4beb0; coding key S2 675def11…2e7a2f |
| Purav's patient review v2.1 | Team's own, fictional cases | Team's own | data/purav_patient_review_v2.1.csv 49077ff1…edd08 |

## Sending these to the OpenAI API

- The two MIMIC sets used here are PhysioNet's **open-access demos**. PhysioNet's guidance against
  sending MIMIC to services such as the OpenAI API ("Responsible use of MIMIC data with online
  services like GPT", https://physionet.org/news/post/gpt-responsible-use, and
  https://physionet.org/news/post/llm-responsible-use/) applies to **credentialed** data under the
  PhysioNet Credentialed Health Data License. The demos carry no data use agreement; ODbL sets
  attribution and share-alike terms only.
- Credentialed MIMIC-IV-ED and MIETIC (credentialed) were **not** used.
- The KTAS data are CC BY 4.0: use, sharing and adaptation are allowed with credit.
- No team member's or real patient's identifiable information was sent. All sources are
  de-identified by their publishers.

## Credit (required by the licences)

- Johnson A, Bulgarelli L, Pollard T, Celi LA, Horng S, Mark R. MIMIC-IV-ED Demo (version 2.2). PhysioNet (2023). https://doi.org/10.13026/jzz5-vs76
- Johnson A, Bulgarelli L, Pollard T, Horng S, Celi LA, Mark R. MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet (2023). https://doi.org/10.13026/dp1f-ex47
- Pollard T, Moody BE, Lehman L, et al. PhysioNet as a global platform for biomedical research. Nature Health (2026). https://doi.org/10.1038/s44360-026-00096-z
- Moon SH, Shim JL, Park KS, Park CS. Triage accuracy and causes of mistriage using the Korean Triage and Acuity Scale. PLoS ONE 14(9): e0216972 (2019).

`cases/mimic.jsonl` is a derived database of the MIMIC demos and is shared under ODbL v1.0;
`cases/ktas*.jsonl` are adapted from the KTAS S1 Appendix under CC BY 4.0.
