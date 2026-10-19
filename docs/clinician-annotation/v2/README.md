# Clinician form v2

This is a new blank form, not an edit of the original annotated workbook or live Google Sheet. Send [Annotation_Notebook_v2.xlsx](Annotation_Notebook_v2.xlsx) with the short [clinician guide](CLINICIAN_GUIDE.md). Pilot Cases 1–3 before collecting the remaining answers.

## What changed

- Three visible tabs only: Patient review, Attention exercise, Form feedback.
- EMS Field Report / Updated EMS Field Report / EMS to ED Handoff terminology.
- Earlier reports precede the latest report, with relative time labels. No actual timestamps are invented.
- Same 10 unchanged `pilot_v1` source cases. Age/sex remain explicitly unreported. No original clinician answers or added hypothetical facts are copied in.
- Meaningful change and change direction are separate. Improvement and measurement uncertainty must not become deterioration labels automatically.
- Draft attention-to-review categories replace preparation terminology. They are not ESI, diagnoses, numerical risk, or validated triage classes.
- The optional attention exercises use the same three-patient group memberships but **new scenario definitions**. Artificial preparation bays/teams have been removed; unknown resources remain unknown. There is no implication that all ED workflow is a single queue.
- Each attention exercise permits ties, inability to assess, and an explicit statement that ranking is not meaningful. Each patient has space for reasoning.
- Reviewer expertise, input uncertainty, and source grounding are explicit. Patient questions and form-design feedback are separate.
- No review-status or approval boxes for the clinician. Yellow clinical answers and reviewer metadata are blank.

## Versions and evaluation compatibility

| Component | Version |
|---|---|
| Workbook/input form | `clinician_form_v2` |
| Clinical source reports | `pilot_v1` (unchanged) |
| Draft attention rubric | `review_attention_v2_draft` |
| Attention exercises | `attention_v2` (`A01`, `A02`) |

**Do not run this workbook through the v1 importer or mix its labels with v1 results.** `evals/annotations.py` expects the old tabs, labels, preparation categories, and resource scenarios. The separate [v2 offline adapter and smoke workflow](../../../evals/V2_EVALUATION.md) support partial intake, source validation, pending approvals, direction, and attention exercises. [V2 LLM runs](../../../evals/V2_LLM_RUNS.md) provide matching model prompts/schemas, opt-in provider calls, and offline re-scoring. The original notebook and `python -m evals run` remain v1; they have not been silently migrated.

Attention categories are:

- `Immediate review`
- `Review soon`
- `Routine awareness`
- `Cannot assess`

The guide defines their intended meaning. Obtain an ED triage/charge nurse or emergency physician's feedback on the task and definitions before freezing a rubric. Record each reviewer's actual role accurately; domain feedback is not physician validation. If clinical scope is insufficient, abstention must remain available.

Do not crosswalk these labels to `Prepare now / Can wait / Routine / Unsure`, numerical triage classes, or existing queue rankings automatically. Added demographics for future cases require a new source-dataset version and new independent labels. Do not ask a reviewer to infer missing demographic details in these cases.

## Team workflow

1. Preserve the annotated original and this blank v2. If used in Google Sheets, import v2 as a **new** spreadsheet, not over the original.
2. Agree use and scope with the reviewer. This form describes synthetic prototype evaluation, not model-weight training or direct clinical use.
3. Ask for independent review of Cases 1–3. Do not disclose expected labels. Resolve rubric/wording issues before requesting the rest.
4. Maintain an internal record keyed by case/exercise ID: reviewer alias/role, source dataset, form/rubric version, review date, completeness, open issues, approval, and provenance. Completion is not approval.
5. Preserve original answers. Any confirmed changes go into a separate reviewed reference copy with an audit trail; do not silently repair clinical labels.
6. Freeze development references and obtain a fresh independently reviewed test set after prompt tuning. These reused cases are not a new independent holdout.
7. Treat this as a small synthetic pilot. It does not establish real-world safety, clinical validity, improved outcomes, or actual ED time saved. No source audio is available, so this is not speech-recognition evaluation.

## Build and verification

`build_v2.mjs` uses the bundled `@oai/artifact-tool` runtime and reads only `../pilot_cases.json`. It never reads the clinician's annotated workbook or makes a model call. Source text is preserved exactly.

The build writes the XLSX and a source-cell/input-cell manifest. Preview and verification files go under `outputs/clinician-v2-20261016/`. The source manifest is not an annotation/reference file and contains no approved answers. The generated manifest also records a SHA-256 of the source JSON, and the builder's assertions require all reviewer-answer cells to be blank.

Before sending, inspect all three tabs, confirm dropdowns and blank answers, and check the first and last source cases. Google Sheets import/dropdown fidelity must be checked after import; native Sheets has not been modified by this build.
