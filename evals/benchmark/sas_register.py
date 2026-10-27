"""Register the app's AI in SAS Model Manager, with a model card built from the benchmark.

    python -m evals.benchmark.sas_model         # first: SAS's baseline model comparison
    python -m evals.benchmark.sas_register      # project "Eir AI triage" in the Public repository

Creates the project if it is missing and replaces the model "Eir AI triage suggestion" in it: the
model's metadata (algorithm, target, inputs, outputs), its key results as properties, and three
files: MODEL_CARD.md, metrics.json and the prompt the model runs with. The model itself runs at
OpenAI; this is its governance record, not something SAS can score.
"""
import asyncio
import dataclasses
import json
import sys
from collections import Counter
from pathlib import Path

from backend import sas_sync
from .stats import fisher_exact
from .to_sas import NO_LEVEL, PREFIX, RESULTS, build_tables

PROJECT = "Eir AI triage"
MODEL = "Eir AI triage suggestion"
PROMPTS = Path(__file__).parents[1] / "prompts"
PROJECT_TYPE = "application/vnd.sas.models.project+json"
MODEL_TYPE = "application/vnd.sas.models.model+json"


def share(k, n):
    return f"{k:,}/{n:,} ({k / n:.0%})" if n else "n/a"


def fairness_lines(calls: list) -> list:
    """Per dataset and variable, urgent visits put at 3-5 and given no level, with Fisher's exact p."""
    lines = []
    for dataset in ("ktas", "mimic"):
        urgent = [r for r in calls if r["DATASET"] == dataset and r["PRIMARY"] and r["REFERENCE_URGENT"]]
        for column in ("SEX", "AGE_BAND"):
            groups = sorted(Counter(r[column] for r in urgent).items())
            for outcome, label in (("Given 3-5", "put at 3-5"), (NO_LEVEL, "no level")):
                events = Counter(r[column] for r in urgent if r["URGENT_OUTCOME"] == outcome)
                (a, n1), (b, n2) = groups
                p = fisher_exact(events[a], n1, events[b], n2)
                lines.append(f"{dataset.upper()} by {column.lower().replace('_', ' ')}, {label}: "
                             f"{a} {events[a]}/{n1}, {b} {events[b]}/{n2} (Fisher p {p:.3f}"
                             f"{', significant after 8 tests' if p < 0.05 / 8 else ''})")
    return lines


def model_card(metrics: dict, tables: dict, comparison: list) -> str:
    run, esi = metrics["runs"][0], metrics["esi"]
    head = {(r["SET_NAME"], r["RATER"], r["OUTCOME"]): r for r in tables["HEADLINE"]}
    ktas = "KTAS ambulance arrivals"
    urgent_n = head[(ktas, "Eir AI", "Given 1-2")]["SET_VISITS"]
    ems = esi["ktas/ems"]["ai_vs_reference"]
    kappa = ems["kappa_quadratic"]
    ops = metrics["operations"]["assessment"]
    consistency = metrics["consistency"]["ktas"]["identical_esi_all_repeats"]
    numbers = metrics["guardrail_mutation_tests"]["handoff_number_check"]["caught_when_wrong_value_not_in_report"]
    baseline = "\n".join(
        f"| {r['MODEL']} | {r['READS']} | {r['URGENT_CAUGHT']} | {r['URGENT_HELD']} | {r['URGENT_MISSED']} "
        f"| {r['NOT_URGENT_FLAGGED']} of {r['HOLDOUT_NOT_URGENT']} |" for r in comparison)
    return f"""# Model card: {MODEL}

## What it is
- Model **{run['scoring_model']}** (OpenAI), reasoning effort `{run['reasoning_effort']}`, with the app's prompt
  `{run['assessment_prompt']}` (sha256 `{run['assessment_prompt_sha256'][:16]}…`), called by `backend.case_assessment`
  at app commit `{run['git_commit'][:7]}`.
- Reads the EMS crew's report (radio transcript and typed notes) and the earlier report the hospital acknowledged.
  Returns a suggested ESI level (1-5, or Cannot assess), its reason, a risk score (1-10) and a handoff summary.

## Intended use
- A suggestion shown to emergency department staff beside each incoming patient. Clinicians can change the ESI level.
- The hospital's Standard queue order ignores it; it orders the queue only when a hospital turns on AI-assisted order.
- "Cannot assess" is shown as Needs review, never as a level. Numbers the reports don't state are flagged.
- Not for: triage without a clinician, diagnosis, or any use without the crew's report.

## Evaluation data (benchmark run {run['started_at'][:10]})
- KTAS (Moon et al. 2019, PLoS ONE, CC BY 4.0): 1,267 real ED visits, expert KTAS as the reference.
- MIMIC-IV-ED Demo v2.2 (PhysioNet, ODbL): 207 stays from 56 patients, triage nurse ESI as the reference.
- 10 fictional cases labelled by one clinician (Purav).
- Reports were written from triage fields: shorter and cleaner than real radio, no speech-to-text step.

## Results (primary run)
- Of {urgent_n} KTAS ambulance arrivals experts rated 1-2: Eir put {head[(ktas, 'Eir AI', 'Given 1-2')]['VISITS']} at 1-2,
  held {head[(ktas, 'Eir AI', 'No level (Needs review)')]['VISITS']} for review, placed
  {head[(ktas, 'Eir AI', 'Given 3-5')]['VISITS']} at 3-5. Triage nurses placed
  {head[(ktas, 'Triage nurses', 'Given 3-5')]['VISITS']} at 3-5.
- Exact level agreement is modest: KTAS ambulance {share(ems['exact']['k'], ems['exact']['n'])}, within one level
  {share(ems['within_one']['k'], ems['within_one']['n'])}, weighted kappa {kappa['value']:.2f}
  ({kappa['low']:.2f}-{kappa['high']:.2f}). It leans urgent: over-triage {share(ems['over_triage']['k'], ems['over_triage']['n'])}.
- On MIMIC its exact agreement is no better than always guessing ESI 2.
- Same input three times: identical ESI for {share(consistency['k'], consistency['n'])} of KTAS ambulance cases.
- Handoff number check caught {share(numbers['k'], numbers['n'])} of injected wrong numbers the reports did not contain.
- Median {ops['latency_ms_median'] / 1000:.1f} s per assessment; about ${ops['list_price_usd_per_call']:.4f} per call at list price.

## Baseline: a SAS model on the numbers alone
SAS gradient boosting (100 trees, CAS `decisionTree.gbtreeTrain`) on age, sex, arrival, mental state and six vitals,
trained on 70% of the KTAS visits and compared on the same 30% holdout; the matched cutoff is tuned on that holdout, which favours the model (`Public.{PREFIX}FEATURES`, `_PartInd_ = 1`).

| Who | Reads | Urgent caught | Held for review | Urgent missed | Non-urgent flagged |
| --- | --- | --- | --- | --- | --- |
{baseline}

## Fairness (SAS `fairAITools.assessBias` and Fisher's exact test)
Urgent visits put at 3-5, by group:
{chr(10).join('- ' + line for line in fairness_lines(tables['CALLS']))}

Eight two-sided Fisher tests (2 datasets x 2 variables x 2 outcomes). The AI did not put one group's urgent
patients at 3-5 significantly more often. It did give no level (Needs review) to more urgent KTAS patients under 65
than 65 and over. The counts are small; these are the checks to repeat on live data.

## Limits
- No result is clinical validation. KTAS is not ESI; use it for "urgent flagged urgent", not ESI accuracy.
- The MIMIC intervals treat repeat visits as independent. Reasons were not graded. Transcription was not tested.

Source: `evals/benchmark/results/REPORT.md` and `metrics.json` in the Eir repository.
"""


def properties(metrics: dict, tables: dict) -> list:
    run, ems = metrics["runs"][0], metrics["esi"]["ktas/ems"]
    head = {(r["SET_NAME"], r["RATER"], r["OUTCOME"]): r for r in tables["HEADLINE"]}
    ktas = "KTAS ambulance arrivals"
    n = head[(ktas, "Eir AI", "Given 1-2")]["SET_VISITS"]
    pairs = {
        "Prompt version": run["assessment_prompt"],
        "Prompt sha256": run["assessment_prompt_sha256"],
        "Reasoning effort": run["reasoning_effort"],
        "App commit": run["git_commit"][:7],
        "Benchmark run": run["started_at"][:10],
        "KTAS ambulance urgent - put at 1-2": share(head[(ktas, "Eir AI", "Given 1-2")]["VISITS"], n),
        "KTAS ambulance urgent - held for review": share(head[(ktas, "Eir AI", "No level (Needs review)")]["VISITS"], n),
        "KTAS ambulance urgent - put at 3-5": share(head[(ktas, "Eir AI", "Given 3-5")]["VISITS"], n),
        "KTAS ambulance urgent - nurses put at 3-5": share(head[(ktas, "Triage nurses", "Given 3-5")]["VISITS"], n),
        "KTAS ambulance - within one level": share(ems["ai_vs_reference"]["within_one"]["k"],
                                                   ems["ai_vs_reference"]["within_one"]["n"]),
        "KTAS ambulance - weighted kappa": f"{ems['ai_vs_reference']['kappa_quadratic']['value']:.2f}",
        "Fairness": "No significant gap in urgent put at 3-5; more no-level for KTAS urgent under 65 (see card)",
        "Clinical validation": "None",
    }
    # Model Manager allows only letters, digits, spaces, _ - and . in property names.
    return [{"name": name, "value": str(value), "type": "string"} for name, value in pairs.items()]


async def fetch_comparison(client) -> list:
    session = await client.open_session()
    try:
        result = await client.action(session, "table.fetch", {
            "table": {"caslib": client.settings.caslib, "name": PREFIX + "MODEL_COMPARISON"}, "to": 100, "maxRows": 100})
    finally:
        await client.close_session(session)
    table = result["results"]["Fetch"]
    names = [column["name"] for column in table["schema"]]
    return [dict(zip(names, row)) for row in table["rows"]]


def _first(response) -> dict:
    """A create returns the new object, or (for models) a one-item collection holding it."""
    body = response.json()
    return body["items"][0] if "items" in body else body


async def register(client, settings, card: str, props: list, metrics: dict) -> str:
    url = settings.url + "/modelRepository"
    repository = next(r for r in (await client.get("/modelRepository/repositories", "repositories"))["items"]
                      if r["name"] == "Public")
    projects = (await client.get(f"/modelRepository/projects?filter=eq(name,'{PROJECT}')", "projects"))["items"]
    if projects:
        project = projects[0]
    else:
        project = _first(await client._call("POST", f"{url}/projects", "create project", headers={"Content-Type": PROJECT_TYPE},
                                      content=json.dumps({
                                          "name": PROJECT, "repositoryId": repository["id"],
                                          "folderId": repository["folderId"], "function": "classification",
                                          "targetLevel": "binary", "targetVariable": "REFERENCE_URGENT",
                                          "targetEventValue": "1", "classTargetValues": "1,0",
                                          "description": "Eir's AI triage suggestion and the baseline it is compared "
                                                         "with. Target: reference triage level 1-2 (urgent)."})))
    existing = (await client.get(f"/modelRepository/projects/{project['id']}/models?limit=100", "models"))["items"]
    for model in existing:
        if model["name"] == MODEL:
            await client._call("DELETE", f"{url}/models/{model['id']}", "replace model")
    run = metrics["runs"][0]
    model = _first(await client._call("POST", f"{url}/models", "create model", headers={"Content-Type": MODEL_TYPE},
                                content=json.dumps({
                                    "name": MODEL, "projectId": project["id"],
                                    "description": "Suggests an ESI level (1-5 or Cannot assess) and a risk score "
                                                   "from the EMS crew's report. Runs at OpenAI; governance record "
                                                   "with its benchmark results. See MODEL_CARD.md.",
                                    "function": "classification", "algorithm": "Large language model",
                                    "tool": "Python", "modeler": "Eir team", "scoreCodeType": "None",
                                    "targetVariable": "REFERENCE_URGENT", "targetEvent": "1", "targetLevel": "binary",
                                    "eventProbVar": "AI_URGENT", "trainTable": f"{settings.caslib}.{PREFIX}CALLS",
                                    "inputVariables": [
                                        {"name": "CURRENT_TRANSCRIPT", "role": "input", "type": "string", "level": "nominal"},
                                        {"name": "PRIOR_INFORMATION", "role": "input", "type": "string", "level": "nominal"},
                                        {"name": "ETA_MINUTES", "role": "input", "type": "decimal", "level": "interval"}],
                                    "outputVariables": [
                                        {"name": "ESI_LEVEL", "role": "output", "type": "string", "level": "nominal"},
                                        {"name": "RISK_SCORE", "role": "output", "type": "integer", "level": "interval"},
                                        {"name": "AI_URGENT", "role": "output", "type": "decimal", "level": "interval"}],
                                    "properties": props})))
    files = {
        "MODEL_CARD.md": (card.encode(), "text/markdown"),
        "metrics.json": ((RESULTS / "metrics.json").read_bytes(), "application/json"),
        f"{run['assessment_prompt']}.txt": ((PROMPTS / f"{run['assessment_prompt']}.txt").read_bytes(), "text/plain"),
    }
    headers = await client._headers()
    for name, (data, content_type) in files.items():
        response = await client._http.post(f"{url}/models/{model['id']}/contents", headers=headers,
                                           files={"files": (name, data, content_type)}, data={"name": name, "role": "documentation"})
        if response.status_code >= 400:
            raise sas_sync.SasError(f"upload {name}: HTTP {response.status_code}")
    return f"{settings.url}/SASModelManager/projects/{project['id']}"


def main(argv=None) -> int:
    settings = sas_sync.Settings.from_env()
    if not settings.configured:
        print("Set SAS_VIYA_URL and SAS_CLIENT_ID + SAS_CLIENT_SECRET (or SAS_ACCESS_TOKEN) in .env")
        return 1
    metrics = json.loads((RESULTS / "metrics.json").read_text(encoding="utf-8"))
    tables = build_tables()

    async def run():
        client = sas_sync.CasClient(dataclasses.replace(settings, prefix=PREFIX))
        try:
            comparison = await fetch_comparison(client)
            card = model_card(metrics, tables, comparison)
            (RESULTS / "MODEL_CARD.md").write_text(card, encoding="utf-8")
            return await register(client, settings, card, properties(metrics, tables), metrics)
        finally:
            await client.close()
    try:
        where = asyncio.run(run())
    except sas_sync.SasError as error:
        print(f"SAS: {error}")
        return 1
    print(f'Model Manager: "{PROJECT}" / "{MODEL}" with MODEL_CARD.md, metrics.json and the prompt ({where})')
    print(f"Also written: {RESULTS / 'MODEL_CARD.md'}")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    sys.exit(main())
