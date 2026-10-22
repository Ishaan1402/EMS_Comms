# Prepare TF-IDF data for your SAS Data Maker colleague

**Your work:** import the revised annotations, freeze a development train/test split, fit training-only TF-IDF, and hand over one ZIP. **Colleague's work:** synthesize training feature rows in SAS Data Maker and return the table plus generation settings. This preparation workflow performs no SAS upload, synthesis, classifier training, LLM calls, fine-tuning or spending.

Use [the SAS/Jupyter notebook](../analytics/tfidf_data_maker_prep.ipynb) or the terminal commands below. Existing v1/v2 scoring and the application's recording route are preserved.

## 1. Install and place the workbook

From the repository root, in your own Python environment:

```bash
python -m pip install -r evals/requirements-ml.txt
```

Save your unchanged `Annotation_Notebook_V2.1_Clean.xlsx` under `evals/local/`. That directory and `evals/results/` are git-ignored. Do not commit the annotated workbook or generated rows. The importer accepts the `patient_review_v2.1` marker on **Patient review**, including files without worksheet dimension metadata. The revised reports are their own source version; it never substitutes the older committed `pilot_v1` transcripts.

Optional intake-only audit, with no splitting or TF-IDF:

```bash
python -m evals.tfidf_prep import \
  --workbook evals/local/Annotation_Notebook_V2.1_Clean.xlsx \
  --out evals/local/v21-import-01
```

This preserves source text, raw answers, field cell locations and a source checksum. ESI numeric integral cells become integer categories without changing their meaning. Missing/invalid answers and incomplete status are reported, not invented. Archived sheets, queue judgments and feedback are not imported as training examples. No clinical approval is inferred from `Done`.

## 2. Prepare the colleague package

```bash
python -m evals.tfidf_prep prepare \
  --workbook evals/local/Annotation_Notebook_V2.1_Clean.xlsx \
  --confirm-synthetic --acknowledge-provisional-labels \
  --max-features 30 --seed 20261021 --test-fraction 0.3 \
  --out evals/results/tfidf-v21-01
```

Always choose a **new** output directory. Missing consent flags or invalid source/labels prevent export. The synthetic flag is an attestation, not PHI detection. This hackathon configuration is not authorized for real patient data.

For the supplied ten-case workbook, this setting prepares 7 training and 3 development-test rows, with 30 selected features. The local audit exposes class coverage and zero-feature rows. There are no ESI 4/5 examples in this pilot; generation cannot create independent evidence for those missing classes.

The default fixed seeded split keeps original case IDs together, including both reports within a case. It selects a split using only group membership and label coverage, never features or model scores. It retains every observed ESI class in training and favors coverage in test. With more related cases, provide `--group-map PATH.json`: a complete object mapping each case ID to its scenario-family ID. It does **not** discover latent scenario relationships automatically. Use `--test-ids SYN001,SYN003,SYN007` to prescribe original test cases if those IDs respect the chosen groups. Explicit splits that remove an entire observed class from training are rejected.

These previously inspected cases are already development material. A local test split is useful to check the software; it is **not a fresh blinded final benchmark**. Record the split once, do not keep changing seeds/feature limits to improve its score, and obtain fresh independent expert-reviewed cases for a final comparison. In any future cross-validation, repeat splitting, vectorizer fitting and Data Maker training separately inside every training fold; do not reuse this full preparation's vocabulary across folds.

## 3. What to send and keep

Send **only `colleague_handoff.zip`**, or its equivalent `share_with_colleague/` folder:

| File inside ZIP | Purpose |
|---|---|
| `train_features.csv` | The only Data Maker input: continuous `tfidf_0000` … columns plus categorical `esi_level` |
| `feature_dictionary.csv` | Column-to-word/phrase mapping and training-only IDF weights |
| `schema.json` | Exact column order, types, valid ranges and observed target categories |
| `manifest.json` | Training bundle ID, training class counts and file checksums |
| `README.md` | Instructions for the colleague and required return files |

The ZIP is built from an explicit training-only folder. It includes no test rows, patient/case identifiers, reference handoffs, explanations, feedback, reviewer identities or original text reports. Hashes identify the frozen source and vectorizer without exposing their content. Training features may still reflect sensitive source text in a different project; TF-IDF is not anonymization.

Keep the **entire `private/` directory and `preparation_audit.json` local**:

- `private/test_features.csv`: test feature rows plus their ESI targets; never upload to the generator.
- `private/vectorizer.json`: plain JSON vocabulary, IDF, fixed settings and training case IDs. No pickle/joblib execution is needed.
- `private/source_inputs.json`: revised source-only reports for later traceability and LLM input preparation, not v2 LLM-ready artifacts.
- `private/annotation_import.json`: original annotations, locations, issues and provisional status.
- `private/split_manifest.json` and `private/row_mapping.json`: the fixed assignment and CSV row-to-case mapping. Identifiers do not become synthesis variables.
- `preparation_audit.json`: class counts, warnings, zero-feature rows and checksums. A zero feature vector is visible, not silently dropped.

## 4. What TF-IDF means here

**X:** only the reported demographics header and original earlier/latest EMS reports. **y:** clinician-entered ESI level. Reference handoffs, ESI explanations, meaningful-change/direction answers, review status, case IDs, ETAs as extra metadata and reviewer information are excluded from text inputs. ETA mentioned in the actual report remains part of its source text.

The small default engineering cap is 30 features (configurable 1–256). The vectorizer uses word unigrams/bigrams, a token pattern that retains single-digit numbers, training-only maximum document frequency 0.85, sublinear term frequency, smoothed IDF and L2 normalization. Preprocessing version `ems_tfidf_v2` removes a fixed, auditable list of common function words, pronouns and reporting boilerplate (for example `he`, `she`, `have`, `our`, `reports`, `picked`, `t`). It deliberately preserves negation and clinical qualifiers such as `no`, `not`, `without`, `cannot`, `unable`, `before`, `after`, `now`, `only`, `may` and numbers. The exact stop-word list is saved in the vectorizer state and colleague manifest; it is not learned from test reports or selected using ESI labels.

Stop-word removal happens before bigram construction, so a bigram can join words separated by removed words in the original report. Keeping negations available does **not** ensure that clinical relationships, temporal order or treatment response are understood, and frequency-based selection need not include every retained word in the final 30 columns. Report headings are fixed context, not copied labels. There is no label-driven feature selection or ID/answer concatenation. Previously exported `ems_tfidf_v1` states (no stop-word removal) can still be restored exactly; do not mix old and new features, synthetic tables or bundle IDs. Regenerate the training handoff and local test features together into a new directory, keeping the original split and labels unchanged.

The vectorizer is fitted exactly once on original training reports. Test reports only call `transform`; unseen test terms do not enter vocabulary or IDF. The feature limit is not a claim that the small source population can support a reliable synthetic distribution. With very few rows, even 30 features can be too many. This is an explicit feasibility experiment, not a statistical remedy for missing data.

To apply the frozen vectorizer to source-only reports later without refitting:

```bash
python -m evals.tfidf_prep transform \
  --vectorizer evals/results/tfidf-v21-01/private/vectorizer.json \
  --cases evals/results/tfidf-v21-01/private/source_inputs.json \
  --out evals/results/tfidf-transform-01
```

`--cases` must contain `dataset_version: patient_review_v2.1` and a nonempty `cases` list of unique IDs with source-only fields. This example transforms the saved original cases for round-trip checking; it does not designate all of them as new test data. The JSON state is hash-checked and only fixed documented vectorizer settings are restored. Keep the preparation environment/version for reproducibility; hashes detect accidental modification, not malicious replacement.

## 5. What the colleague should return

1. `synthetic_train_features.csv`, **exactly** the input feature columns and `esi_level`, with finite weights in [0,1] and only observed ESI categories. Features and target must be synthesized **jointly**. Do not independently generate labels or round a continuous label into an ESI category afterward.
2. `generation_metadata.json`: source bundle ID, generator/model version, configuration, seed or unavailable flag, input/output row counts, class counts, conditioning/constraints and any filtering or normalization.
3. Data Maker's quality report and duplicate/near-copy warnings. If the small input is unsupported, return that limitation; do not duplicate source rows merely to bypass minimum-row requirements.

Original nonzero TF-IDF vectors have **L2 norm one**, not a sum of one. Synthetic vectors should be assessed for sparsity, ranges, norms and label relationships. Do not silently normalize/filter them, manufacture new ESI classes, interpret numerical columns as actual vitals, or turn synthetic rows into fake clinician annotations. Generating a TF-IDF row does not generate a corresponding patient transcript.

## After the return (separate next step)

Validate provenance/schema, then train the same fixed logistic regression and linear SVM on (a) original training rows, (b) original plus synthetic training rows. Optionally add a majority-class baseline. Keep the same frozen vectorizer/test cases for all comparisons. This preparation implementation intentionally does not train those classifiers yet or expose test performance while choosing the generator.

Report macro F1, balanced accuracy, confusion matrices, weighted kappa, ordinal error and under/overtriage **against provisional reviewer labels**, alongside the tiny denominators. A three-case test cannot establish clinical superiority, calibration or safety. Synthetic rows do not increase the number of independently reviewed test patients. Do not add generated labels as ground truth for held-out original reports.

LLM comparison needs an explicitly versioned **v2.1 ESI prompt/output/scoring contract** using the same original reports. The existing v2 awareness/attention collector is not that contract; no ESI-to-attention crosswalk or automatic clinical winner is implemented here. Clinical classification and handoff factuality remain separate evaluations.

## Verification

```bash
python -m unittest discover -s evals/tests
```

TF-IDF tests require the optional ML dependencies; the original pipeline remains usable without them. Tests use artificial source/annotation fixtures, check training-only fitting, seeded grouping, frozen-state restoration, target exclusion, package isolation, gates, versions and overwrite refusal. An isolated environment is recommended; installing dependencies need not change the app's environment.

Technical references: [TF-IDF vectorizer](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html), [data leakage and preprocessing](https://scikit-learn.org/stable/common_pitfalls.html), and [SAS Data Maker](https://www.sas.com/en_us/software/data-maker.html). Data Maker access is user-confirmed; this code does not assume an undocumented SAS SDK/API, minimum-row allowance, feature capability or license entitlement.
