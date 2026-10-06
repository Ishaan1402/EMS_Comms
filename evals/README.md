# EMS evaluation pipeline

A provider-independent pilot evaluation, **offline by default**. It uses the **same 10 fictional cases and two optional queue exercises sent to the clinician**. Mock scoring and planning need no keys, model calls, or app server. Opt-in live adapters are documented in [the model comparison guide](MODEL_COMPARISON.md).

Start with [the notebook](../analytics/ems_evaluation.ipynb), or run the commands below from the repository root. The application and clinician workbook are unchanged.

## Run it now

Python 3.10+ is sufficient for scoring, reports, mocks, and tests. XLSX import additionally needs:

```bash
python -m pip install -r evals/requirements.txt
python -m evals demo --out evals/results/demo-01
python -m unittest discover -s evals/tests -v
```

Open `evals/results/demo-01/dashboard.html` in a browser. Three **mock** configurations deliberately produce perfect, imperfect, and failed outputs. These are artificial software tests, not model experiments or medical reference answers. Every command requires a **new** output directory and refuses to overwrite an existing run.

The notebook uses the same functions as the CLI. It does not require pandas or a plotting library; Jupyter supplies IPython for displaying its HTML dashboard.

## When the doctor answers

1. Export the current Google Sheet as **Microsoft Excel (.xlsx)**. The committed workbook is an older snapshot, not a live connection. Keep the `Doctor review` and `Queue exercise` tabs and original reports.
2. Save the export under `evals/local/`. Import it:

```bash
python -m evals import-annotations \
  --workbook evals/local/doctor-export.xlsx \
  --out evals/local/import-01
```

3. Review `evals/local/import-01/references.json`. Import preserves partial answers and sets **every approval to false**. `Done` only indicates progress. After checking complete answers and resolving questions with the clinician, add a pseudonymous `reviewer_id`, freeze `rubric_version` (for example `pilot-rubric-v1`), and set `approved` to `true` for each accepted case/queue. Do not invent missing answers or quietly change clinical labels. Preserve an untouched import and save the reviewed copy as `evals/local/approved-references.json`.
4. Collect model predictions in the format below. Do not include reference answers in model inputs. Score:

```bash
python -m evals score \
  --references evals/local/approved-references.json \
  --predictions evals/local/model-predictions.json \
  --out evals/results/experiment-01
```

5. Review summaries separately using the generated `summary_review_template.json`. Then rerun into a new directory with `--reviews evals/local/summary-reviews.json`.

No approved references means quality scores are **N/A**, never fabricated zeros. Source transcripts, ETAs, or queue resource context that differ from the fixtures make the importer fail: version those fixtures and the dataset before evaluating altered cases. Changed instructions/clinical definitions also require a new rubric version.

## Data and prediction contract

`docs/clinician-annotation/pilot_cases.json` supplies `case_id`, `encounter_id`, `update_number`, `elapsed_minutes`, `eta_minutes`, `prior_information`, and `current_transcript`. Some cases include one earlier report; these are **not complete transport trajectories**. The reports are fictional text, not recordings or outputs from a speech recognizer.

`evals/queues.json` supplies two separate snapshots with three case IDs each and the preparation-bay/team constraints stated in the clinician form. Other hospital demand is excluded. Do not infer actual bed availability or transfer destinations.

Case reference fields are `reference_summary`, `meaningful_change`, `explanation_missing_information`, `preparation_category`, `status`, `reviewer_id`, `rubric_version`, and `approved`. An eligible case requires all of these, `Done`, and boolean `true` approval. Queue references additionally require an explanation and complete `order`, with numeric ranks or `Unsure`; ties are allowed.

Model output is exactly five fields. [The candidate prompt](prompts/awareness_v1.txt) defines this **new evaluation contract**, not the application's existing GPT-4 response format. A later app adapter must map outputs explicitly; do not silently treat its numeric risk score as one of these categories.

```json
{
  "kind": "model_predictions",
  "dataset_version": "pilot_v1",
  "configs": [{
    "config_id": "candidate_a",
    "provider": "provider-name",
    "model": "exact-model-snapshot",
    "prompt_version": "awareness_v1",
    "input_contract_version": "pilot_v1",
    "repeats": 1
  }],
  "records": [{
    "config_id": "candidate_a",
    "case_id": "SYN001",
    "repeat_id": 1,
    "status": "ok",
    "output": {
      "summary": "Candidate handoff text, grounded only in the supplied report.",
      "meaningful_change": "No earlier report",
      "change_explanation": "There is no earlier report to compare.",
      "missing_information": "Describe relevant missing or uncertain information, or explicitly state none identified.",
      "preparation_category": "Unsure"
    },
    "latency_ms": null,
    "input_tokens": null,
    "output_tokens": null,
    "cost_usd": null,
    "retry_count": null
  }],
  "queues": [{
    "config_id": "candidate_a",
    "queue_id": "Q01",
    "repeat_id": 1,
    "order": {"SYN001": "Unsure", "SYN004": "Unsure", "SYN007": "Unsure"}
  }]
}
```

This is a **format example**, not a clinically assessed answer or full run. Supply all expected cases/repeats: missing records count as failures. Status is `ok`, `error`, `timeout`, or `refusal`; non-ok records may omit output. Extra output fields, empty narrative strings, or unknown labels fail schema validation.

Allowed change labels: `Yes`, `No`, `Unclear`, `No earlier report`.

Allowed preparation labels: `Prepare now`, `Can wait`, `Routine`, `Unsure`. These describe preparation awareness, not validated triage categories, diagnoses, or autonomous clinical decisions.

Queue predictions must include all group members, with ranks 1–3 or `Unsure`. Ties are permitted. A missing/invalid queue does not disappear from the denominator. An abstention on one patient does not discard other assessable pairs.

Record real latency around the **whole request including retries** and cost for all billable attempts. Missing telemetry stays unavailable; zero means genuinely zero, not unknown. The scorer does not infer prices from model names or make live calls. The separate opt-in runner uses explicit rates from its experiment configuration and labels them as estimates, not invoices. Keep generation settings, model snapshot, prompt, and retry policy with the experiment artifacts. Use the same inputs across candidates; preferably collect three repeats to inspect variability, but do not call 30 runs “30 patients.”

## Metrics

| Task | Standard metrics | Scoring rule |
|---|---|---|
| Meaningful change | Accuracy, Precision, Recall, F1, Confusion Matrix | `Yes` is positive. Doctor `Unclear` / `No earlier report` excluded from binary scoring, not converted to `No`. Candidate abstentions/failures count as incorrect on assessable references. |
| Preparation category | Accuracy, Macro-F1, Confusion Matrix | Four-category agreement, including `Unsure`; Macro-F1 averages reference-supported classes. Report support counts. |
| Missed preparation | False Negative Rate for `Prepare now` | Any other category, abstention, or unavailable response misses a reference `Prepare now`. This measures reference agreement, not real-world patient harm. |
| Queue ordering | Pairwise Accuracy | Correct strict ordering / doctor-assessable strict pairs. Reference ties are assessed separately using tie agreement; doctor `Unsure` pairs excluded. Missing predictions count as incorrect. |
| Summary coverage | Content Recall | Approved human review: matched essential facts / essential facts. |
| Summary factuality | Factual Precision | Supported factual claims / all factual claims, using approved human review. |
| Summary safety | Unsupported Claim Rate, Temporal Error Rate, Uncertainty Error Rate | Fraction of reviewed responses with at least one such error; consequential error counts also reported. |
| Reliability | Usable Response Rate, Schema Validation Rate, Error Rate | Usable/all expected attempts; schema-valid/returned `ok` outputs; nonusable/all expected attempts. Always show all three. |
| Efficiency | Median/P95 Latency, Reported Case Cost | Only supplied telemetry; coverage counts are retained in JSON. Live-run costs are padded estimates, not invoices; the run ledger separately covers case and queue attempts. |

Undefined metrics are null in JSON, blank in CSV, and N/A in HTML. Repeated results and patient pairs are correlated; the report does not generate misleading confidence intervals from them.

### Human summary review

The existing short clinician handoff is **not an exhaustive structured fact inventory**. Summary scoring therefore needs a separate review, not automatic word overlap or an uncalibrated LLM judge. A team reviewer can prepare grades, but accepted grades need review under the frozen clinician-agreed rubric.

For each output, reviewers identify the essential facts in the source/reference and count `critical_facts_total`, `critical_facts_matched`, `claims_total`, `unsupported_claims`, `temporal_errors`, and `uncertainty_errors`; mark boolean `consequential_error`. Claims must preserve time, negation, uncertainty, units, and “planned” versus “given” treatments. Repetition is not another fact. Define how compound claims are split **before** comparing candidates; preferably review blinded to model identity and reconcile ambiguous grades.

Fill `reviewer_id`, `rubric_version`, and `approved` in the review template. Its `prediction_sha256` binds the grade to the exact output; changed outputs require re-review. Reports expose review coverage so a small reviewed subset cannot masquerade as whole-dataset quality. No summary reviews means no summary quality score.

## Baseline and outputs

```bash
python -m evals prepare --out evals/local/prepared-01
python -m evals baseline --out evals/local/baseline-01
```

`prepare` exports normalized cases and blank references. `baseline` returns the current report verbatim, abstains from preparation, and makes no clinical assessment. It is a zero-inference-cost **raw-information comparator**, not a heuristic clinical model. The default mode in the notebook is deliberately mock; switch to clinician mode only with reviewed reference/prediction files.

Each report directory contains:

- `dashboard.html`: comparison, confusion matrices, failures, and side-by-side case review; completely offline.
- `metrics.json`: full metrics, denominators, reference exclusions, and provenance.
- `comparison.csv`: one row per configuration.
- `case_results.csv`: per-case/repeat labels and statuses; free-text summaries remain in JSON/HTML rather than spreadsheet-executable cells.
- `summary_review_template.json`: generated by `score`, ready for human grading.

The CSVs are a simple interface for SAS import and visualization; no SAS server credentials or availability assumptions are needed. This change does **not** implement a live SAS connector or imply a particular service is licensed.

Local annotations and results under `evals/local/`, `evals/results/`, and `analytics/results/` are git-ignored. These defaults avoid publishing review artifacts accidentally; they do not make the app HIPAA compliant. Keep this pilot synthetic. Do not put patient data or provider secrets into committed fixtures, prompts, notebooks, logs, or reports.

## Experimental limits and next stage

These ten cases are a **rubric/pipeline pilot**, not an independently powered clinical study. Do not repeatedly optimize prompts against them and then claim a held-out accuracy. Freeze the rubric, use this pilot for development, and obtain a separate clinician-reviewed test set before making generalization claims. Confidence intervals later should resample encounters, and queue studies should resample independent scenarios—not repeated model runs or pairs from the same queue.

With current inputs we can assess agreement, factuality, failure handling, and limited ordering. We **cannot** measure real deterioration lead time, AUROC, Brier score, calibration, alert burden per transport hour, clinician time saved, rural benefit, speech-recognition quality, or patient outcomes. Those need longitudinal events, independent targets, operational simulation and/or a timed clinician study. Add those as a separate experiment rather than inventing times/outcomes from these transcripts.

Next: approved answers → blinded summary review → matched candidate runs → frozen held-out cases → longitudinal replay evaluation. A stronger future synthetic study should use the same underlying trajectories for static/dynamic workflows, separately vary observation delays and transport time, hold scoring policy fixed, and evaluate alert burden alongside warning time. Such findings remain simulation evidence.

The design follows [task-specific, human-calibrated evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices), while remaining independent of any provider's evaluation platform.

## Implementation map

`contracts.py` validates fixtures/output vocabulary; `annotations.py` reads the clinician form; `evaluate.py` enforces provenance/approval and scores runs; `metrics.py` contains transparent formulas; `report.py` exports artifacts; `demo.py` supplies mocks and the raw comparator; `__main__.py` exposes the CLI. None imports or changes application inference code.
