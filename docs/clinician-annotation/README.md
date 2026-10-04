# Simple clinician review materials

These are the simplified clinician guide and annotation notebook prepared for the synthetic-data hackathon pilot. Application code is unchanged.

## Send to the clinician

- [Live one-page guide](https://docs.google.com/document/d/1FxzWt1KcR_4GmbMPvQWKQGFhMikSTUiUNVdnsdMh0p8/edit)
- [Live annotation notebook — Doctor review](https://docs.google.com/spreadsheets/d/1nP6SPc1bQsoUAJ98RicGrImwexQOp82HhfNSHZxotzM/edit#gid=1003001)

Ask the clinician to start with Cases 1–3 independently, then discuss the questions and draft preparation choices before continuing. Only the yellow answer boxes need to be filled in.

## Repository copies

| File | Purpose |
| --- | --- |
| [Clinician_Guide.pdf](Clinician_Guide.pdf) | One-page export of the simplified Google Doc. |
| [CLINICIAN_GUIDE.md](CLINICIAN_GUIDE.md) | Readable, editable text of the same instructions. |
| [Annotation_Notebook.xlsx](Annotation_Notebook.xlsx) | Export of the simplified Google Sheet with blank clinical answers. |
| [pilot_cases.json](pilot_cases.json) | The 10 fictional source cases, not clinician labels. |

The PDF and XLSX are snapshots exported on 2026-10-03. Later Google Doc/Sheet edits and clinician answers do not automatically sync to Git. Use the live Google Sheet for collaborative review and progress tracking; export reviewed answers separately when ready.

## Notebook layout

- **Doctor review:** one patient at a time; brief ED handoff, important change with a short reason/missing information, and preparation need. A review status tracks progress.
- **Queue exercise:** optional comparison of two groups of three patients. Rank preparation order within each group; ties and uncertainty are allowed.
- The six original detailed tabs remain intact but hidden. They can be unhidden for team work; they are not required for the simplified clinician review.

“Done” tracks reviewer progress, not approved ground truth. It does not populate or finalize the old detailed annotation fields. The team still needs to check completeness, resolve questions, record the reviewer and agreed instructions, and obtain clinician verification of any team-derived structured labels. The simpler form does not provide fact-by-fact extraction labels on its own.

## Source data and limitations

All reports are AI-authored fictional fixtures, not real EMS reports, recordings, or speech-to-text outputs. Some have an earlier report and a current update; none is a full transport trajectory or includes a future outcome. Clinical answers were left blank for independent review. These cases support a small expert-reviewed demo, not clinical validation or evidence of real-world safety or effectiveness.

Each JSON row contains, in order: `case_id`, `encounter_id`, `update_number`, `elapsed_minutes`, `eta_minutes`, `prior_information`, `current_transcript`. Preparation choices are draft project-specific categories, not validated triage scores.
