# Comparing models after prompt tuning

**Architecture ready; no inference calls or model downloads have been made.** We are tuning prompts, not training weights. The two prompt versions are candidate designs, not already-proven improvements.

## What runs where

```text
Versioned fictional cases + experiment settings + versioned prompts
                     ↓
          Plan (no requests or endpoint probes)
                     ↓ explicit execution consent, synthetic-data confirmation
            Provider-independent runner
       ┌──────────┬───────────┬──────────┬────────────┐
       │ OpenAI   │ Anthropic │ Gemini   │ Ollama     │
       │ Responses│ Messages  │ Content  │ localhost  │
       └──────────┴───────────┴──────────┴────────────┘
                     ↓
       Saved outputs, usage, failures, per-attempt ledger
                     ↓              + approved doctor references
             Existing scorer       + separate human summary reviews
                     ↓
       Dashboard, confusion matrices, case review, SAS-friendly CSVs
```

The application's existing GPT-4/Whisper workflow is untouched. This harness deliberately has no database, messaging, app-server, vector-store, or agent dependency. It uses Python's standard library for REST calls; optional XLSX import still uses openpyxl. Only the opt-in runner contacts providers.

## Configured API candidates

Model IDs and rates were checked against official documentation on **October 6, 2026**. They are experiment candidates, not an asserted leaderboard. Availability still depends on the account. Refresh rates and pin snapshots before an actual run.

| Model | Why include it | Configured input / output USD per million tokens |
|---|---|---|
| OpenAI `gpt-6-luna` | Cheap structured-awareness candidate | 0.10 / 0.50 |
| OpenAI `gpt-6.1-sol` | Higher-quality comparator for difficult changes | 2.00 / 10.00 |
| Gemini `gemini-3.1-flash-lite` | Cross-provider low-cost candidate | 0.25 / 1.50 |
| Claude `claude-haiku-4-5-20251001` | Cross-provider fast candidate with a pinned snapshot | 1.00 / 5.00 |

Sources: [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing). These are standard short-text list rates, not promises about credits, free tiers, account limits, or invoices. Gemini's documented free tier may help a synthetic demo, but do not assume a key belongs to that tier; its free-tier data-use policy differs from paid use.

The adapters use documented schema-constrained generation: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), [Claude JSON outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Gemini generation configuration](https://ai.google.dev/api/generate-content), and [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs). Schema constraints do not ensure medical correctness; refusals, truncation, enum errors, and semantic mistakes remain possible.

## Best open-weight candidates for this project

**My first local experiment would be Qwen3.5-9B.** This is a workload/engineering recommendation, not a finding that it beats the others on EMS data. Prefer post-trained/instruction-following checkpoints; a bare pretrained model is not an interchangeable chat candidate. “Open-weight” is precise here: a permissive weights license does not mean all training data and training code are public.

| Candidate | Role in our project | Practical tradeoff |
|---|---|---|
| **Qwen3.5-9B** | First local awareness, change-summary, and JSON candidate | Apache-2.0; relatively small, supports thinking/nonthinking modes. Test nonthinking first for latency; instruction/long-context benchmark performance is a reason to test it, not clinical evidence. |
| **Gemma 4 26B-A4B** | Independent local-family comparator | Apache-2.0; MoE with about 25B total / 4B active parameters and native system-role support. Active parameters are not the memory footprint: all expert weights still need storage/residency. |
| **GPT-OSS-20B** | Local reasoning/structured-output comparator | Apache-2.0; model card describes a 16GB-memory deployment with MXFP4. Low reasoning effort may still consume output budget; use a runtime that handles Harmony correctly. |
| **Qwen3.5-27B** | Larger Qwen upgrade if 9B fails difficult cases | Apache-2.0. More memory/latency; test rather than assuming bigger guarantees faithful medical summaries. Not in the default run. |
| **Qwen3.5-4B** | Smaller-device fallback to benchmark | Smaller download/runtime; require the same omission, uncertainty, and false-negative tests. Not a default quality substitute. |
| **MedGemma 1.5 4B** | Optional medical-domain comparison, not our first default | Custom Health AI Developer Foundations terms, not Apache-2.0. Its card warns about validation requirements and lack of multi-turn optimization. Domain branding does not establish suitability for our longitudinal awareness task. |

Primary sources: [Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B), [Qwen3.5-27B card](https://huggingface.co/Qwen/Qwen3.5-27B), [Gemma 4 card](https://huggingface.co/google/gemma-4-26B-A4B), [GPT-OSS-20B card](https://huggingface.co/openai/gpt-oss-20b), [MedGemma card](https://huggingface.co/google/medgemma-1.5-4b-it).

The [Ollama Qwen library](https://ollama.com/library/qwen3.5) lists roughly 6.6–7.6GB downloads for 9B, 3.3–4GB for 4B, and 17–20GB for 27B. Those are **download sizes, not total RAM requirements**. Runtime buffers, context, GPU offload, quantization, and other apps matter. I could not verify this computer's RAM from the sandbox, so no model-fit guarantee is made. Gemma's “4B active” similarly does not imply a 4B-sized download. Start with short context, benchmark peak memory, and select only models the machine can comfortably run.

Serving recommendation: **Ollama now**, a dedicated inference server later if throughput requires it. The implemented local adapter is loopback-only. It does not automatically install a runtime, pull weights, enable cloud inference, or send reports to Hugging Face. GPU-server/vLLM/Bedrock/Vertex/Azure adapters are not implemented by this change. Self-hosting can reduce data disclosure; it does not by itself establish HIPAA compliance.

## Plan safely now

From the repository root:

```bash
python -m evals plan --out evals/results/model-plan-01
python -m evals run --models qwen9b --out evals/results/qwen-plan-01
```

**Both commands only plan.** `run` without `--execute` never calls a model, even if keys are configured. The notebook also contains a plan-only preview cell.

The default matrix is seven models × two awareness prompts × three repeats × (ten case calls + two queue calls) = **504 requests**, without retries. Start with one local and one API candidate later, not necessarily the whole matrix. The runner randomizes job order with a fixed scheduling seed, not a claim of deterministic LLM outputs. Switching local models can incur load time; latency includes load/retry time, and Ollama duration metadata is retained. For final latency claims, distinguish cold from warm runs and benchmark each local model separately.

`awareness_v1` is the original candidate. `awareness_v2` emphasizes current versus earlier findings, comparable measurement conditions, treatment status, negation, uncertainty, and concise change-focused handoffs. `queue_v1` is fixed across awareness variants; its independent queue calls assess ordering directly from original reports, not propagated awareness outputs. Repeating the queue call across configurations tests variance, **not an effect of awareness prompt v2 on ranking**. A later integration experiment must explicitly test awareness → deterministic ranking or awareness → queue model as a different workflow.

## Execute later, only when authorized

Do not execute these commands until a runtime/model or API credentials have been configured and you intend to generate outputs. Keys belong in environment variables, not experiment JSON, notebook cells, CLI arguments, or committed `.env` files. This harness deliberately does not read `.env` automatically.

Example local command, **not run during setup**:

```bash
python -m evals run --models qwen9b \
  --execute --confirm-synthetic --max-usd 0 --max-requests 72 \
  --out evals/results/qwen-run-01
```

It assumes Ollama already runs on `127.0.0.1:11434` and the model is installed. Check the installed model's supported thinking values using Ollama's `/api/show` before execution; tags/runtime behavior can change. The runner never silently drops an unsupported candidate or repairs a response by another LLM.

For API runs, export the appropriate `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` in your shell. Also require `--confirm-pricing` and a positive `--max-usd` chosen after examining the plan. No spend is authorized merely by a saved configuration. The runner validates all selected credentials and limits before contacting a provider.

Costs are **padded list-rate estimates** from supplied usage, not actual bills. Missing usage/ambiguous failures stay unknown; the reservation still charges the full padded allowance internally. Gemini thought tokens count as output. Retries default to zero; optional retries apply only to selected HTTP transient statuses, not timeout, refusal, or malformed JSON. Maximum attempts and a preflight padded reservation limit bound the planned workload, but a provider billing cap is still needed: pricing changes or unexpected billing can defeat a client estimate. Hardware costs are excluded for local runs.

Outputs: `plan.json`, `predictions.json`, `attempts.jsonl`, `run_summary.json`, `summary_review_template.json`, and `report/`. The ledger includes **all case and queue attempts**; dashboard case-cost metrics intentionally cover case requests only. An interrupted run saves completed predictions; missing predictions remain failures. Keys and HTTP error bodies are never logged. Raw model text is saved locally for failure analysis, so keep all inputs synthetic.

## How we decide which is best after prompt tuning

1. **Development:** keep these ten pilot cases for understanding failures and comparing v1/v2. Doctor reference answers are never serialized into requests. Diagnose omissions, invented facts, incorrect treatment/temporal interpretation, schema failures, and missed preparation needs. Human-grade summaries blinded to provider; do not award an automatic “best” based only on categorical accuracy.
2. **Prompt tuning:** revise rules or add examples using a development set only. Create a new prompt file/version rather than editing a frozen version. Inspect the frozen prompt text, hash, input hash, settings, and actual returned model identifier in each run. Equalize the number of tuning rounds per provider; otherwise compare “model + tuning effort,” not models alone.
3. **Freeze:** choose each candidate's prompt/settings and the reference rubric. Obtain a separate clinician-reviewed test set with no patient/scenario-template overlap. Set `purpose` to `holdout` and supply `tuning_encounter_ids` listing every previously seen encounter. The planner rejects explicit overlap; undeclared exposure and near-duplicate templates still need manual auditing.
4. **Final comparison:** compare the frozen candidates on identical held-out inputs and repeated calls. Review critical-fact recall, factual precision, consequential errors, change recall/F1, prepare-now misses, and queue pairwise accuracy. Only then consider latency, cost, memory, and failure rate among acceptable candidates. Agree quality/safety thresholds with the clinician before inspecting results.
5. **Selection:** choose a local or cheap API model for awareness if it meets those criteria; keep a stronger fallback only if it demonstrably helps difficult cases. Do not automatically promote a “winner” from ten development examples or use an opaque weighted score that hides important misses. This implementation provides comparable artifacts and metrics, not an invented winner or clinical validation.

Once references arrive:

```bash
python -m evals score \
  --references evals/local/approved-references.json \
  --predictions evals/results/qwen-run-01/predictions.json \
  --reviews evals/local/summary-reviews.json \
  --out evals/results/qwen-scored-01
```

The human review file must match the exact prediction hashes. Without it, summary quality remains N/A even if categorical metrics are available. Without approved doctor answers, generation can test schema/latency/failures but **cannot determine clinical agreement**.

## Verification boundary

Tests use fake responses and injected transports, so they cannot spend money or download models. They cover request shapes, schema parsing, refusals, truncation, duplicate/malformed JSON, thinking-token accounting, credential/log protections, retries, partial failures, and execution/limit gates. **No live endpoint smoke test, local inference benchmark, provider account-access check, or clinical model comparison has occurred.** Do one authorized single-case smoke test per provider before a large run, since API/runtime capabilities can change.
