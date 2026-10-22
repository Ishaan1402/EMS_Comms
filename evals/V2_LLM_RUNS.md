# V2 LLM calls and model comparison

**Want one key instead of several?** Use the [OpenRouter setup](OPENROUTER.md) and `--config evals/experiments_openrouter_v2.json`. It compares four models with only `OPENROUTER_API_KEY`; the direct-provider configuration below remains available.

The v2 collector uses the existing OpenAI Responses, Claude Messages, Gemini Generate Content, and loopback Ollama adapters. It sends the **six-field v2 awareness schema** and a separate **attention_v2 rank schema**, never the old preparation categories. No provider SDK or app server is required. It does not change the application's recording route.

Implementation has been exercised with fake transports, not live credentials. Account/model access, model-specific settings, available quotas, and real latency/reliability still need a separately authorized live smoke test. No real API call is made by importing this module, running tests, or planning.

## 1. Plan without calling a model

From the repository root (including SAS JupyterHub):

```bash
python -m evals.llm_v2 plan \
  --models openai_luna,claude_haiku,gemini_lite,qwen9b \
  --out evals/results/v2-plan-01
```

The default `experiments_v2.json` selects only SYN001 and SYN002, one prompt, one repeat, no attention exercises. The command above therefore plans **8 requests**. All seven configured candidates would plan 14. `run` without `--execute` also only writes a plan. No model downloads or local-server probes occur. Plans record source-only input snapshots/hashes, prompts/hashes, requested model/settings, v2 versions, request counts, and a padded cost reservation.

Candidate IDs/model strings and example rates were copied from the earlier experiment configuration, not newly benchmarked or account-verified. The file explicitly marks the copied prices as needing rechecking. Do not interpret them as today's guaranteed rate or proof of access. Preserve/pin a model snapshot when available for your final experiment.

To alter the experiment, copy `experiments_v2.json` into an ignored local directory and use `--config`:

- `case_ids`: explicitly selected source cases.
- `models`: candidate names, provider settings, and verified input/output rates.
- `prompt_versions`: v2 awareness prompt filenames (must start `awareness_form_v2`).
- `repeats`: repeat each candidate/prompt on the same cases; reports keep repeats separate.
- `queue_ids`: `[]` by default, or `A01`/`A02` when all members are included in `case_ids`.
- `queue_prompt_version`: a v2 attention prompt starting `attention_form_v2`.
- `purpose`: development, or holdout with an explicit `tuning_encounter_ids` declaration. Reused pilot cases are not a fresh independent holdout.

Never put API keys, reference answers, or reviewer feedback in this JSON. Unknown configuration/model fields are rejected. The source whitelist is exactly the seven `pilot_v1` case fields. Annotated workbook columns and reviewer identities do not enter prompts. There is no model-as-judge or automatic clinical winner selection.

## 2. Supply credentials and authorize execution deliberately

Keys are read from the executing process environment:

| Provider | Environment variable |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Claude | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY` |
| OpenRouter (optional one-key configuration) | `OPENROUTER_API_KEY` |
| Local Ollama | No API key; configured model must already be installed and served on loopback |

In a Jupyter notebook, a hidden-input cell can set a key for subprocesses launched **from that same kernel**:

```python
import os
from getpass import getpass
os.environ["OPENAI_API_KEY"] = getpass("OpenAI API key: ")
```

Do not print environment variables, commit keys, or paste keys into notebook outputs. This does not configure an unrelated terminal process. Use the environment's approved secret mechanism for terminal runs; ask the mentor whether outbound APIs are permitted from SAS. Keys are not read from a `.env` file automatically by the evaluation runner.

After checking the account/model settings and updating rates, this is an **example opt-in command, not a command already executed**:

```bash
python -m evals.llm_v2 run \
  --models openai_luna \
  --references evals/local/v2-import-01/references.json \
  --execute --confirm-synthetic --confirm-pricing \
  --max-usd 1 --max-requests 2 \
  --out evals/results/v2-openai-01
```

`--references` is optional. Imported unapproved/partial references may be provided; they stay unapproved and quality scores stay N/A. Reference versions/IDs are checked before any call. Source edits in a reference block its quality scoring but do not become model input: generation always uses the unchanged canonical source reports.

In Jupyter, run the same command with `!python ...` after setting the key in that kernel, or use `subprocess.run([...], check=True)`. Use a new output directory each time.

For a local model already available on the same host:

```bash
python -m evals.llm_v2 run \
  --models qwen9b --execute --confirm-synthetic \
  --max-usd 0 --max-requests 2 \
  --out evals/results/v2-local-01
```

Ollama URLs are loopback-only, without credentials/query/path. A local server/model on your laptop is not automatically accessible from a remote SAS JupyterHub. The runner does not install models or provision GPUs; token fees of zero do not imply zero compute cost.

Every execution needs `--execute`, `--confirm-synthetic`, a finite nonnegative `--max-usd`, and sufficient `--max-requests`. Commercial calls additionally need a positive budget, `--confirm-pricing`, and all selected keys—even if using credits. Gates run before creating an execution directory or contacting an endpoint. The budget is a padded reservation, **not a hard provider-side billing guarantee**; use account-side spending controls too. The synthetic flag is an attestation, not PHI detection. Do not use real patient information with this hackathon configuration.

## 3. Read collected outputs and reports

Each run writes, under the ignored output directory:

- `plan.json`: frozen versions, source-only input snapshot, prompts, model/settings and estimated reservation.
- `attempts.jsonl`: all actual attempts, statuses, usage and cost estimates, including retries. Keys and HTTP error bodies are not logged.
- `predictions.json`: shared execution engine's transport envelope. **Not a v1 clinical evaluation artifact; do not feed this file to the v1 scorer.**
- `predictions_v2.json`: explicit v2 batch envelope with post-validation status, actual reported model, raw output, schema errors, latency, token usage and estimated costs. Invalid responses are retained as failures, not repaired or silently replaced.
- `run_summary.json`: total attempt count and estimated costs for both awareness and attention requests, unknown-billing counts, and any stop condition. Estimates are not invoices.
- `report/dashboard.html` and `report/comparison.csv`: one comparison row/report per candidate, prompt, and repeat. Detailed reports include collected outputs for human review. Repeats are correlated, not independent patients.
- `report/<configuration>.repeat-N/predictions.json`: a single-config/single-repeat v2 prediction envelope.

Case metrics retain expected-case denominators if a response fails or is missing. Median/P95 latency and reported case cost include retries; cost/latency coverage counts remain visible, and unknown cost is not treated as zero. These per-case cost measures exclude the separate attention calls; use `run_summary.json` for the full run. Summary factual precision/content recall remain N/A pending a separate v2 human-grading contract. No word-overlap proxy is substituted.

Timeouts, refusals, truncation, malformed JSON, extra/wrong fields, empty narratives, old preparation labels, wrong rank vocabularies, and contradictory direction fields are not accepted as usable v2 outputs. There is no cross-provider fallback during comparison. Retries default to zero and are bounded to selected transient HTTP errors when explicitly configured; ambiguous timeouts are not retried automatically. Ctrl-C preserves partial transport/v2 artifacts and reports when possible. There is no automatic resume or overwrite.

## 4. Re-score later without more API calls

When annotations are completed and independently approved, preserve the original import and make a reviewed reference copy as described in `V2_EVALUATION.md`. Then:

```bash
python -m evals.llm_v2 score \
  --run evals/results/v2-openai-01 \
  --references evals/local/v2-reviewed-references.json \
  --out evals/results/v2-openai-rescored-01
```

This reads saved v2 outputs and the frozen source snapshot, verifies versions/source hashes/canonical case text, and regenerates reports for the exact original subset. It does not invoke adapters, need API keys, or charge for inference. Outputs from changed cases or a changed rubric must not be reused as though the experiment were unchanged. Unknown/duplicate batch IDs and v1 envelopes are rejected rather than silently dropped.

Import `report/comparison.csv` into SAS Visual Analytics for candidate/prompt/repeat comparison. Keep unavailable quality metrics blank, show approved-reference counts, and separate clinical agreement from response reliability, latency and cost. Detailed per-repeat `case_results.csv` and `exercise_results.csv` can be loaded separately. Do not sum percentages or count repeated model outputs as additional patients.

## API references checked during implementation

- [OpenAI structured outputs / Responses](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses): strict schema, required properties, nullable direction, no additional fields; application checks remain necessary.
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs): schema through `output_config.format`.
- [Gemini Generate Content structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output?hl=en): REST schema through `generationConfig.responseFormat.text`.
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs): JSON schema through `format` on local chat requests.

Documentation verification is not a live provider smoke test or evidence of clinical quality. No model-provider change, PHI authorization, fine-tuning, or production-compliance claim is implied by this integration.
