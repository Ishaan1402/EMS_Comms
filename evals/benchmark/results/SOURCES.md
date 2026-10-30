# Sources, licences and permitted use

Checked 2026-10-26. Files were downloaded on Ishaan's Mac on 2026-10-26 05:55 UTC; the checksums
below are verified by `build_cases.py` on every build.

| Source | Access | Licence | Files used (sha256) |
|---|---|---|---|
| MIMIC-IV-ED Demo v2.2, https://physionet.org/content/mimic-iv-ed-demo/2.2/ | Open access, no credentialing or data use agreement ("Anyone can access the files, as long as they conform to the terms of the specified license") | Open Data Commons Open Database License v1.0 (LICENSE.txt sha256 bcdfcd4f31c790e30fb645dc36414907172405897adff329ad379eed85d12017) | ed/edstays.csv.gz fc185cb1…3a5c52, ed/triage.csv.gz 086dadcb…e09b (both match PhysioNet's SHA256SUMS.txt) |
| MIMIC-IV Demo v2.2, https://physionet.org/content/mimic-iv-demo/2.2/ | Open access | ODbL v1.0 (same LICENSE.txt) | hosp/patients.csv.gz 59dcc6cd…f6fae (ages only) |
| Moon SH et al. 2019, PLOS ONE 14(9): e0216972, doi 10.1371/journal.pone.0216972, S1 Appendix | Public, part of the article ("All relevant data are within the paper and its Supporting Information files") | Creative Commons Attribution 4.0 | S1_EMR_review_coding_data.xlsx f631c951…4beb0; coding key S2 675def11…2e7a2f |
| CDC/NCHS NHAMCS 2022 emergency department public-use file, https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Dataset_Documentation/NHAMCS/ (documentation doc22-ed-508.pdf) | Public-use file, downloaded 2026-10-27 | Public-use data under the NCHS data use agreement: statistical analysis and reporting only, no attempt to identify anyone, cite NCHS as the source (https://www.cdc.gov/nchs/policy/data-user-agreement.html) | nhamcs/ed2022_sas.sas7bdat 2f9aa045…1c71c; ed22for.txt d7f2b37c…1b65339 |
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

## Literature cited in the videos

- National Center for Health Statistics. National Hospital Ambulatory Medical Care Survey: 2022 emergency department summary tables (Table 4) and 2022 ED public-use data file. CDC/NCHS.
- Carter AJ et al. Information loss in emergency medical services handover of trauma patients. Prehosp Emerg Care 2009;13(3):280-5. PMID 19499462.
- Lin CB et al. Emergency medical service hospital prenotification is associated with improved evaluation and treatment of acute ischemic stroke. Circ Cardiovasc Qual Outcomes 2012;5(4):514-22. PMID 22787065.
- Studnek JR et al. The impact of emergency medical services on the ED care of severe sepsis. Am J Emerg Med 2012;30(1):51-6. doi:10.1016/j.ajem.2010.09.015.
- Seymour CW et al. Time to treatment and mortality during mandated emergency care for sepsis. N Engl J Med 2017;376:2235-44. doi:10.1056/NEJMoa1703058.
- Bennett CL et al. Preventive health services offered in a sampling of US emergency departments, 2022-2023. West J Emerg Med 2024;25(5):823-7 (NEDI-USA count of 5,613 US EDs).
- US FDA. Clinical Decision Support Software: Guidance for Industry and Food and Drug Administration Staff (final Sept 2022; current version Jan 29, 2026).
