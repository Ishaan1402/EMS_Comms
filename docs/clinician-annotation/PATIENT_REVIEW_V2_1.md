# Patient review v2.1

Use the separately delivered `Annotation_Notebook_v2.1.xlsx` for the revised cases.
The original `Annotation_Notebook.xlsx`, Google Sheet and `pilot_cases.json` remain the older pilot.
The matching new source cases are [patient_review_v2_1_cases.json](patient_review_v2_1_cases.json).
All patients and added details are fictional; clinical answers are blank for independent review.

## For the physician

This is a single review with no follow-up round: complete the yellow fields for all ten cases.
The Attention exercise and Form Feedback tabs are optional, as in the v2 sheet.
Read the earlier report, then the latest (T=0). Use only supplied facts and flag important gaps;
missing information does not mean normal. Answer for the report as written and note anything that
seems unrealistic for an EMS crew on the Form Feedback tab. Judge change separately
from preparation needs. Improvement can count too. Choose **No earlier report** when none is
provided; direction is optional. The handoff is 2–3 sentences, what you would want to hear before arrival.

Rate preparation and awareness before arrival:

- **Prepare now:** preparation/escalation of awareness should begin without delay.
- **Can wait:** preparation matters, but more time-sensitive patients can come first.
- **Routine:** awareness only; no special preparation need is established by the report.
- **Cannot assess:** insufficient reliable information to assess.

Mark each case **Done**, or **Question for team** if something stops you answering.

## Maintainer notes

The preparation choices match the application's question. They are draft labels, not validated
triage classes or instructions to assign beds or treatment.

There are no extra acknowledgment questions. The existing replay supplies that technical setup.
Known patient details above each report also appear in the report when they become available.
Case 6's age and estimated weight are supplied after treatment, not in its earlier report.
Reports state age and sex once, as a crew would; the source of each detail is in the row above
the report. Every case gives age, sex and a weight estimate. Five are **detailed** (history,
medicines, allergies and social habits from the patient: SYN001, SYN003, SYN006, SYN007, SYN009)
and five are **basic** (only key or bystander facts, crew weight estimate: SYN002, SYN004, SYN005,
SYN008, SYN010). The case file also carries `age_years`, `sex`, `weight_kg`, `weight_source` and
`history_detail` for analysis; the model never receives these fields.
The layout is the v2 sheet's three tabs; Patient review differs only in the preparation labels and a
Review status row. The importer reads Patient review and, with `--queues`, the Attention exercise (A01:
SYN001/004/007, A02: SYN002/008/010).
The hidden `Original v2` tab preserves the supplied earlier responses; do not reuse them as labels for v2.1.
Report units: pulse in beats/min, respirations in breaths/min, BP in mmHg, oxygen saturation in
percent, glucose in mg/dL, temperature in Celsius and weight in kg. Reports identify oxygen
device/flow and the source of weight estimates where applicable.

## Import completed answers

Save a working copy as `evals/local/patient-review-v2.1.xlsx`. From the repository root:

```bash
python -m evals import-annotations \
  --workbook evals/local/patient-review-v2.1.xlsx \
  --cases docs/clinician-annotation/patient_review_v2_1_cases.json \
  --queues docs/clinician-annotation/patient_review_v2_1_queues.json \
  --approve-complete \
  --out evals/local/patient-review-v2.1-import-01
```

`--approve-complete` approves every case marked Done with a handoff, change, preparation and at
least one explanation, under the reviewer alias and the dataset version, and prints any case or
attention group it left unapproved. `--queues` reads the Attention exercise groups (A01, A02);
"Cannot assess" there is stored as Unsure. Generate the replay with the same `--queues` file so the
app's queue order is snapshotted for the same groups.

The importer checks the dataset version, case IDs, patient details, report text, report intervals
and ETAs against the selected fixtures. It reads only `Patient review`, never the archived answers.
Partial responses and both explanation fields are preserved. The displayed `Cannot assess` is
explicitly stored as the existing evaluation contract's `Unsure`, with the display answer retained
separately. `Done` is progress, not approval: every imported `approved` remains false and reviewer
and rubric verification must still be completed before scoring.

The selected fixtures declare `patient_review_v2.1`; imported references and prepared files retain
that version. Versioned case sets do not silently inherit the original pilot's queue exercises.
An explicit `--queues` file is needed to add a separately agreed queue exercise. Source edits
require a revised fixture set and version; do not translate the former review-urgency answers
into preparation categories.

## Prepare matching app inputs offline

```bash
python demo/pilot_scenario.py \
  --pilot docs/clinician-annotation/patient_review_v2_1_cases.json \
  --queues docs/clinician-annotation/patient_review_v2_1_queues.json \
  --out evals/local/patient-review-v2.1-scenario.json
```

This generates a scenario without starting the application or calling a model. Current reports
are copied exactly; earlier reports lose only the introductory relative-time phrase because the
replay supplies the timing. Its existing earlier-report acknowledgment setup is unchanged.
The old ten-case pilot remains available with the old defaults.

When separately authorized, use this scenario for an application run and score its predictions
with `--cases docs/clinician-annotation/patient_review_v2_1_cases.json` and approved v2.1 references.
Do not reuse `pilot_v1` predictions or references. Tests of import and replay preparation establish
software compatibility, not clinical accuracy or evidence of time saved or patient benefit.
