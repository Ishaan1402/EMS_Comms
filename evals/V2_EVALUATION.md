# V2 partial-annotation evaluation

The v2 form has a separate read-only importer and offline scorer. V1 imports, prompts, provider adapters, and scoring retain their existing behavior. No automatic v1/v2 crosswalk is performed.

## Run a pipeline check now

Upload an exported v2 workbook into `evals/local/` in your local checkout or SAS JupyterHub, then run from the repository root:

```bash
python -m unittest discover -s evals/tests
python -m evals.v2 smoke \
  --workbook evals/local/partial-v2.xlsx \
  --out evals/results/v2-smoke-01
```

Use a new output directory for every run. Requires Python 3.10+ and optional `openpyxl` from `evals/requirements.txt`; the rest of the v2 workflow uses the standard library. The v2 blank form at `docs/clinician-annotation/v2/Annotation_Notebook_v2.xlsx` must accompany the code because it is the frozen layout reference.

This command does **not** call an LLM, probe endpoints, download a model, train anything, or require credentials. It:

1. Reads the supplied workbook without editing it and records source/template SHA-256 hashes.
2. Imports filled and blank answers separately, preserving entered text, reviewer metadata, and extra feedback notes. `Form feedback` capitalization differences are allowed.
3. Checks all source reports, displayed times/ETAs, age/sex placeholders, fixed wording, and question layout against the blank form and canonical `pilot_v1` cases. Static value changes are blocking issues on the affected reference, not silently accepted input facts. Changed question rows fail with an explicit form-version error. Formulas/error cells are rejected, not evaluated.
4. Leaves every reference `approved: false` and `rubric_frozen: false`. A filled answer is not proof of clinical correctness. Completeness counts are not reviewer approvals.
5. Generates a deterministic **raw-current-report / abstention baseline**, directly from the canonical source cases. It never sees clinician answers or feedback. This is not a competitive AI model or an LLM-quality result.
6. Runs scoring, exclusion rules, and report exports. With no eligible approved references, quality metrics remain null/N/A, not zero. The baseline's usable-response rate describes software output formatting only.

Outputs:

- `dashboard.html`: intake coverage, exclusions, source edits, and metric availability.
- `metrics.json`: full report with exclusion reasons and source/form issues.
- `comparison.csv`, `case_results.csv`, `exercise_results.csv`: controlled fields for SAS Visual Analytics; unavailable metrics remain blank.
- `references.json`: private original annotations, source hashes, feedback, and pending approval fields.
- `baseline_predictions.json`: deterministic source-only predictions.

All outputs stay under ignored `evals/results/` by default. Do not publish private reviewer identity or completed annotations automatically. No annotated workbook is added to Git. Source values and narrative annotations remain in local JSON/HTML, not the CSVs.

## Import without generating predictions

```bash
python -m evals.v2 import \
  --workbook evals/local/partial-v2.xlsx \
  --out evals/local/v2-import-01
```

Import preserves answers outside a dropdown vocabulary as entered and marks the relevant content invalid; it does not map them to the nearest option. A partial workbook can still be imported and audited. Source/form edits must be resolved explicitly before affected references can score.

## Approval and later real evaluation

Keep the original imported file unchanged. After independent review, save a reviewed copy and record, for each accepted case/exercise: pseudonymous `reviewer_id`, the matching `rubric_version`, `reviewed_at`, a meaningful `approval_note`, and boolean `approved: true`. Set top-level `rubric_frozen: true` only after the team and reviewer agree to freeze the displayed rubric. Never set these merely to make metrics appear. New wording or categories require a new form/rubric contract, not quiet edits to these version identifiers.

Case eligibility requires all five core answer fields: `summary`, `meaningful_change`, `evidence_missing_information`, `attention_category`, and `attention_reason`. If meaningful change is `Yes`, `change_direction` is also required. It must be `Improved`, `Worsened`, `Mixed`, `Other`, or `Unclear`. References with incomplete fields, invalid vocabulary, unresolved source edits, missing approval metadata, or an unfrozen rubric are excluded with reasons. Eligible cases can score even while other cases remain unfinished; excluded counts remain visible.

Attention labels remain `Immediate review / Review soon / Routine awareness / Cannot assess`. Do not convert them into preparation categories, ESI scores, or treatment order. Meaningful-change binary scoring uses reference Yes/No only; Unclear and No earlier report are not negatives. Change direction is assessed separately, so detecting improvement is not counted as deterioration.

Exercise eligibility separately requires explicit approval/provenance, acceptable source context, `ranking_meaningful` equal to `Yes` or `Only some comparisons`, all three rank entries (1–3 or `Cannot assess`), a group explanation, and a reason for each patient. Intentional ties are allowed. A response that ranking is not meaningful is preserved, not forced into ordering. Pairwise accuracy uses strictly ordered assessable reference pairs; ties and uncertain pairs are reported separately. Missing model orders count as failures on assessable pairs.

For precomputed outputs matching v2, run:

```bash
python -m evals.v2 score \
  --references evals/local/v2-reviewed-references.json \
  --predictions evals/local/v2-model-predictions.json \
  --out evals/results/v2-score-01
```

The prediction envelope uses `kind: model_predictions_v2`, the matching four version fields from the import, one `config` with `config_id`, `provider`, and `model`, and one record per expected case ID. Each record has `status` (`ok`, `error`, `timeout`, or `refusal`) and, for successful responses, an `output` with exactly the six v2 answer fields above. Exercise outputs use `exercises: [{exercise_id: A01, order: {SYN001: 1, SYN004: 2, SYN007: 3}}]` with quoted strings in actual JSON. Missing/failed/malformed outputs remain in the expected-case denominator. Version/provenance mixing and duplicate/unknown IDs are rejected. This first v2 path scores one configuration with one output per case; use separate report directories for different configurations.

**The original `python -m evals run` comparison runner still targets v1.** Do not give it v2 references or relabel its outputs as v2. The new [v2 LLM collector](V2_LLM_RUNS.md), `python -m evals.llm_v2`, uses matching v2 prompts/schemas, shared gated provider adapters, per-candidate/repeat reports, and offline re-scoring of saved outputs. Planning never calls models. No live model comparison is performed by the v2 smoke command. Summary factual precision/content recall remain N/A until a separate v2 human-grading contract is implemented; no word-overlap substitute is used. Actual LLM latency/cost are not fabricated from baseline timing.

## SAS dashboard

Import the three CSVs from a run into separate SAS tables. Label the report `OFFLINE V2 PIPELINE CHECK - NOT LLM PERFORMANCE`. Leave blank quality metrics blank and show `approved_reference_cases` alongside them. Do not combine these with v1 mock metrics or join aggregate measures onto case rows and sum them.

## Tests

Unit tests use artificial in-memory references to exercise approved/partial scoring, failures, direction, ties, and rejection gates. Those fixture approvals never touch the uploaded answers and are not saved as clinician references. The integration tests read the blank v2 workbook without changing it and exercise import, smoke, score, and no-overwrite behavior.
