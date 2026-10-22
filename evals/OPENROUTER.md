# One-key model comparison

OpenRouter is now an optional provider in the **evaluation pipeline**, not a change to the live application's recording route. One `OPENROUTER_API_KEY` and OpenRouter credit balance cover all four candidates in `experiments_openrouter_v2.json`; no upstream OpenAI/Google/Anthropic keys are needed. Existing direct adapters and local Ollama remain available. No new Python dependency is required.

## Start with a free offline plan

```bash
python -m evals.llm_v2 plan \
  --config evals/experiments_openrouter_v2.json \
  --out evals/results/openrouter-plan-01
```

Default: two synthetic cases × four models = **8 planned requests**, no network/model calls. Use `--models or_gemini_lite` for just one candidate, or comma-separated IDs. Copy the config to `evals/local/` to select more cases/repeats or matching A01/A02 attention exercises.

When ready, create an OpenRouter account/key, add credits, and set a key-level credit cap. Store the key in the process environment through your approved secret mechanism, never in git, config JSON, notebook outputs, or chat. In SAS JupyterHub, a hidden-input cell can set it for commands launched from that same kernel:

```python
import os
from getpass import getpass
os.environ["OPENROUTER_API_KEY"] = getpass("OpenRouter API key: ")
```

Ask the mentor whether outbound OpenRouter access is allowed. A notebook environment variable does not configure an unrelated terminal. No `.env` file is automatically loaded.

**Example only: this command spends credits and has not been executed.** Recheck endpoint availability/rates first. From a terminal with the key set (or prefix with `!` in the same notebook kernel):

```bash
python -m evals.llm_v2 run \
  --config evals/experiments_openrouter_v2.json \
  --execute --confirm-synthetic --confirm-pricing \
  --max-usd 1 --max-requests 8 \
  --out evals/results/openrouter-run-01
```

Optionally add `--references PATH_TO_V2_REFERENCES_JSON`. Partial/unapproved annotations remain unapproved; clinical quality scores stay N/A. Saved outputs can be rescored later without more calls, as in [V2_LLM_RUNS.md](V2_LLM_RUNS.md). The report includes reliability, latency, cost and annotation agreement where valid; no automatic clinical winner is declared.

## Prices checked October 19, 2026

USD per **million** text tokens, for the specific configured endpoints:

| Candidate ID | Model | Pinned serving provider | Input | Output |
|---|---|---|---:|---:|
| or_gemini_lite | Gemini 2.5 Flash-Lite | google-ai-studio | $0.10 | $0.40 |
| or_gpt_mini | GPT-4.1 Mini | openai | $0.40 | $1.60 |
| or_haiku | Claude Haiku 4.5 | anthropic | $1.00 | $5.00 |
| or_qwen32b | Qwen3 32B (open weights, hosted FP8) | deepinfra/fp8 | $0.08 | $0.28 |

Sources: public endpoint catalogs for [Gemini](https://openrouter.ai/api/v1/models/google/gemini-2.5-flash-lite/endpoints), [GPT](https://openrouter.ai/api/v1/models/openai/gpt-4.1-mini/endpoints), [Claude](https://openrouter.ai/api/v1/models/anthropic/claude-haiku-4.5/endpoints), and [Qwen](https://openrouter.ai/api/v1/models/qwen/qwen3-32b/endpoints). Each advertised JSON Schema/response-format support at inspection, not proof of live account access or exact schema acceptance. These are inexpensive initial comparison candidates, not a claim that they are the newest/best models. Hosted Qwen still costs money; running open weights locally remains a separate option.

[OpenRouter pricing](https://openrouter.ai/pricing): inference deducts posted model costs from credits. Standard has a **5.5% fee on credit purchases**, not a second per-request token surcharge. Buying $10 of credits is approximately $10.55 before taxes. Business is 8%; it is unnecessary for this synthetic pilot. No Standard monthly subscription/minimum spend is listed. Selected `:free` models have rate limits, but these paid pinned candidates are **not free**. Free routing has different availability/capabilities and is not enabled by this config.

Example, assuming **1,000 input + 300 output tokens per case/model**, no tools, caching, extra reasoning, retries or failures:

- 100 cases × all four candidates (400 calls): **$0.3764 inference**, about **$0.40 including the proportional Standard purchase fee**, before taxes.
- Current 2-case default (8 calls): approximately **$0.00753 inference** under the same token assumptions.

Real prompts include schemas/system instructions; output and hidden reasoning may increase billed tokens. Longer reports, more prompts/repeats and attention exercises multiply costs. The offline planner deliberately reserves much more than this illustrative average; `--max-usd` is not a hard billing guarantee. Use OpenRouter account/key credit caps too.

## Fair comparisons and cost records

- Fixed HTTPS endpoint; key only in Authorization header. One explicit model and one provider slug per candidate, `only` + `order`, `allow_fallbacks: false`, `require_parameters: true`. No automatic model selection or cross-provider rescue. Base provider slugs can include provider endpoint variants; use a variant tag where available and inspect returned metadata. Alias IDs may still change upstream; retain run timestamps and actual returned model/provider.
- Strict JSON Schema requested, followed by existing local v2 validation. No repair/healing plugin or context transforms. No tools/search. Unsupported schema/parameters/endpoints fail visibly rather than silently dropping constraints.
- Configured input/output rates also become OpenRouter `max_price` endpoint filters. If rates rise above those caps, requests should fail instead of accepting the higher endpoint rate. This is not a total-invoice cap; provider-side spending controls are still necessary.
- Original reports only; reviewer identities/answers never enter requests. No hidden reasoning text or HTTP error bodies are saved. Provider errors inside HTTP-200 responses, refusals and truncation are not usable outputs. No automatic retry of ambiguous timeouts.
- Records retain requested settings, actual model/provider when returned, request ID, tokens and OpenRouter's reported `usage.cost`. `cost_usd` for OpenRouter is reported credit deduction, **excluding purchase fees/taxes**; missing cost stays unknown, even if tokens are known. The ledger separately retains padded list-rate estimates. `run_summary.json` exposes known reported API costs and unknown-cost attempts; detailed per-case reports exclude separate attention calls, so use the run summary for the whole run. Do not add reported costs to their estimates: they describe the same requests.
- Only synthetic data is authorized. This integration makes **no HIPAA/BAA/compliance claim**. Private data would require a separate review of OpenRouter, upstream providers, account logging/retention, contracts and the broader application.

API references: [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs), [provider selection](https://openrouter.ai/docs/guides/routing/provider-selection), [response/usage format](https://openrouter.ai/docs/api/reference/overview). Tests use artificial transports only; no live model smoke test has been performed.
