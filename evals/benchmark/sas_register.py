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


def fairness_tests(calls: list) -> list:
    """The same Fisher tests sas_fairness.py saves as EIR_BENCH_BIAS_TESTS, from the same counts."""
    from .sas_fairness import SENSITIVE, TEST_FAMILIES, tests
    datasets = {"KTAS, all visits": "ktas", "MIMIC, all stays": "mimic", "NHAMCS, all visits": "nhamcs"}
    groups = []
    for scope in TEST_FAMILIES:
        urgent = [r for r in calls if r["DATASET"] == datasets[scope] and r["PRIMARY"] and r["REFERENCE_URGENT"]]
        for column in SENSITIVE:
            for value in sorted({r[column] for r in urgent if r[column]}):
                rows = [r for r in urgent if r[column] == value]
                groups.append({"SCOPE": scope, "SENSITIVE_VARIABLE": column, "GROUP_VALUE": value,
                               "URGENT_VISITS": len(rows),
                               "GIVEN_3_5": sum(r["URGENT_OUTCOME"] == "Given 3-5" for r in rows),
                               "NO_LEVEL": sum(r["URGENT_OUTCOME"] == NO_LEVEL for r in rows)})
    return tests(groups)


def _label(t: dict) -> str:
    return (f"{t['SCOPE'].split(',')[0]} by {t['SENSITIVE_VARIABLE'].lower().replace('_', ' ')}, "
            f"{'put at 3-5' if t['OUTCOME'] == 'Put at 3-5' else 'no level'}")


def fairness_lines(calls: list) -> list:
    lines = []
    for t in fairness_tests(calls):
        flag = f", significant after {t['TESTS_RUN']} tests" if t["SIGNIFICANT"] else ""
        lines.append(f"{_label(t)}: {t['GROUP_A']} {t['A_EVENTS']}/{t['A_URGENT']}, "
                     f"{t['GROUP_B']} {t['B_EVENTS']}/{t['B_URGENT']} (Fisher p {t['FISHER_P']:.3f}{flag})")
    return lines


def fairness_summary(calls: list) -> str:
    """One sentence for the card's limitations and the Fairness property, from the tests themselves."""
    found = fairness_tests(calls)
    significant = [t for t in found if t["SIGNIFICANT"]]
    if not significant:
        return f"Fairness: no significant gap by group in {len(found)} Fisher tests (small counts)."
    return "Fairness: " + "; ".join(
        f"{t['SCOPE'].split(',')[0]} urgent patients got {'no level' if t['OUTCOME'] != 'Put at 3-5' else 'put at 3-5'} "
        f"{t['GROUP_A']} {t['A_EVENTS']}/{t['A_URGENT']} vs {t['GROUP_B']} {t['B_EVENTS']}/{t['B_URGENT']} "
        f"(Fisher p {t['FISHER_P']:.3f})" for t in significant) + \
        f"; no other gap was significant in {len(found)} tests."


def outcome_table(rows: list) -> str:
    """Card rows for all KTAS visits: each outcome group, with the AI, nurses and experts side by side."""
    counts = {(r["OUTCOME_GROUP"], r["RATER"], r["PLACED"]): r for r in rows if r["SCOPE"] == "KTAS all visits"}
    lines = []
    for group in dict.fromkeys(r["OUTCOME_GROUP"] for r in rows):
        def placed(rater, outcomes):
            return " / ".join(str(counts[(group, rater, o)]["VISITS"]) for o in outcomes)
        total = counts[(group, "Eir AI", "Given 1-2")]["GROUP_VISITS"]
        lines.append(f"| {group} | {total} | {placed('Eir AI', ('Given 1-2', NO_LEVEL, 'Given 3-5'))} "
                     f"| {placed('Triage nurses', ('Given 1-2', 'Given 3-5'))} "
                     f"| {placed('KTAS experts', ('Given 1-2', 'Given 3-5'))} |")
    return "\n".join(lines)


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

## Patient outcomes (KTAS, what happened after triage)
A triage level is a judgement, even the experts'. The KTAS source also records each visit's disposition:

| Patients who | Visits | Eir: 1-2 / no level / 3-5 | Triage nurses: 1-2 / 3-5 | KTAS experts: 1-2 / 3-5 |
| --- | --- | --- | --- | --- |
{outcome_table(tables['OUTCOMES'])}

## Fairness (SAS `fairAITools.assessBias` and Fisher's exact test)
Urgent visits put at 3-5, by group:
{chr(10).join('- ' + line for line in fairness_lines(tables['CALLS']))}

{fairness_summary(tables['CALLS'])[len('Fairness: '):]} Eight two-sided Fisher tests (2 datasets x 2 variables
x 2 outcomes), significant below 0.05 / 8. A visit with no level goes to Needs review, so a clinician sees it. The counts
are small; these are the checks to repeat on live data.

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
        "Fairness": fairness_summary(tables["CALLS"])[len("Fairness: "):],
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


async def register(client, settings, card: str, props: list, metrics: dict, limitation: str = "") -> str:
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
    run = metrics["runs"][0]
    files = {
        "MODEL_CARD.md": (card.encode(), "text/markdown"),
        "metrics.json": ((RESULTS / "metrics.json").read_bytes(), "application/json"),
        f"{run['assessment_prompt']}.txt": ((PROMPTS / f"{run['assessment_prompt']}.txt").read_bytes(), "text/plain"),
    }
    existing = (await client.get(f"/modelRepository/projects/{project['id']}/models?limit=100", "models"))["items"]
    model = next((m for m in existing if m["name"] == MODEL), None)
    if model:
        # Update in place: the model card text people typed in Model Manager (purpose, intended use,
        # limitations, the embedded report) lives on this model and must survive a re-run.
        await _update(client, url, model["id"], props, files, limitation)
        return f"{settings.url}/SASModelManager/projects/{project['id']}"
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
    await _upload(client, url, model["id"], files)
    return f"{settings.url}/SASModelManager/projects/{project['id']}"


async def _upload(client, url: str, model_id: str, files: dict) -> None:
    headers = await client._headers()
    for name, (data, content_type) in files.items():
        response = await client._http.post(f"{url}/models/{model_id}/contents", headers=headers,
                                           files={"files": (name, data, content_type)}, data={"name": name, "role": "documentation"})
        if response.status_code >= 400:
            raise sas_sync.SasError(f"upload {name}: HTTP {response.status_code}")


async def _update(client, url: str, model_id: str, props: list, files: dict, limitation: str) -> None:
    """Replace our properties and files on the model; keep every other field and property as it is."""
    response = await client._call("GET", f"{url}/models/{model_id}", "read model")
    model = response.json()
    ours = {p["name"] for p in props}
    model["properties"] = [p for p in model.get("properties") or [] if p["name"] not in ours] + props
    if limitation and limitation not in (model.get("limitations") or ""):
        model["limitations"] = " ".join(filter(None, [model.get("limitations"), limitation]))
    await client._call("PUT", f"{url}/models/{model_id}", "update model",
                       headers={"Content-Type": MODEL_TYPE, "If-Match": response.headers["etag"]},
                       content=json.dumps(model))
    contents = (await client.get(f"/modelRepository/models/{model_id}/contents?limit=100", "files"))["items"]
    for content in contents:
        if content["name"] in files:
            await client._call("DELETE", f"{url}/models/{model_id}/contents/{content['id']}", f"replace {content['name']}")
    await _upload(client, url, model_id, files)


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
            return await register(client, settings, card, properties(metrics, tables), metrics,
                                  fairness_summary(tables["CALLS"]))
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
