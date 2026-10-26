"""Mutation tests of the two deterministic guardrails, run on the model's real outputs. Offline.

1. Handoff number check (backend.case_assessment.unsupported_numbers): in each real handoff,
   every number the reports do support is replaced, one at a time, by a wrong value (about 15%
   higher and lower; +/-1 for temperatures, +2 below 20). Caught = the check names the wrong value.
   A wrong value that the report happens to state elsewhere (e.g. pulse 96 changed to 136 when the
   BP is 136/82), or for a temperature also as its °F/°C conversion, cannot be caught by design;
   those are counted separately as collisions.
2. Extraction quote check (backend.case_extraction.interpret): in each real extraction, every kept
   vital sign gets a wrong value with its true quote (should be dropped), and separately a quote
   that is not in the report (the app should fail the run and store nothing).

These measure the checkers, not the model: the model's own unsupported numbers are counted on the
unmodified outputs in score.py.
"""
import copy
import os

os.environ.setdefault("EIR_DB_PATH", os.devnull)  # importing the backend opens no database
from backend import case_assessment, case_extraction  # noqa: E402


def wrong_values(text: str) -> list:
    value = float(text.replace(",", ""))
    if "." in text:
        candidates = [value + 1.0, value - 1.0]
        return [f"{c:.1f}" for c in candidates if c > 0]
    if value >= 20:
        candidates = {round(value * 1.15), round(value * 0.85)}
    else:
        candidates = {value + 2}
    return [str(int(c)) for c in sorted(candidates) if c != value and c > 0]


def number_mutants(fields: dict, inputs: dict):
    """(original, wrong, caught, collision) for each supported number in the summary."""
    summary = fields["summary"]
    flagged = set(case_assessment.unsupported_numbers(fields, inputs))
    given = inputs["input"]
    stated = case_assessment.numbers_in(f"{given['prior_information']}\n{given['current_transcript']}")
    stated |= {float(given[k]) for k in ("eta_minutes", "elapsed_minutes") if given[k] is not None}
    for match in case_assessment.DIGITS.finditer(summary):
        original = match.group()
        if original in flagged:
            continue  # already unsupported in the real output
        # The check reads a number as a temperature (and allows °F/°C conversions) by its context.
        temperature = bool(case_assessment.DEGREES_AFTER.match(summary, match.end())
                           or case_assessment.TEMPERATURE_BEFORE.search(summary[:match.start()]))
        for wrong in wrong_values(original):
            mutated = {**fields, "summary": summary[:match.start()] + wrong + summary[match.end():]}
            caught = wrong in case_assessment.unsupported_numbers(mutated, inputs)
            yield {"original": original, "wrong": wrong, "caught": caught, "temperature": temperature,
                   "collision": case_assessment.stated(float(wrong), stated, temperature=temperature)}


def fabricated_vital(fields: dict, inputs: dict):
    """A whole invented reading appended to the handoff, with a value the reports never state."""
    given = inputs["input"]
    stated = case_assessment.numbers_in(f"{given['prior_information']}\n{given['current_transcript']}")
    value = next(v for v in range(131, 200) if float(v) not in stated)
    mutated = {**fields, "summary": f"{fields['summary']} HR {value}."}
    return str(value) in case_assessment.unsupported_numbers(mutated, inputs)


def extraction_mutants(output: dict, info: dict):
    """Results of the two extraction mutations for one real model output."""
    results = []
    try:
        baseline = case_extraction.interpret(output, info)
    except case_extraction.UnverifiedSourceQuote:
        return results
    if baseline is None:
        return results
    kept = {(f["vital_name"], f["vital_value"]) for f in baseline if f["finding_type"] == "vital"}
    for index, entry in enumerate(output["vitals"]):
        if (entry.get("name"), case_extraction._number(entry.get("value", 0))) not in kept:
            continue
        quote_numbers = case_assessment.numbers_in(entry.get("quote", ""))
        wrong = next(v for v in (entry["value"] + d for d in (7, 11, 13, 17, 19, 23))
                     if not case_assessment.stated(float(v), quote_numbers, temperature=entry["name"] == "temp_c"))
        low, high = case_assessment.VITAL_SIGNS[entry["name"]][2:]
        if not low <= wrong <= high:
            continue
        mutated = copy.deepcopy(output)
        mutated["vitals"][index]["value"] = wrong
        facts = case_extraction.interpret(mutated, info)
        stored = any(f["vital_name"] == entry["name"] and f["vital_value"] == case_extraction._number(wrong)
                     for f in facts or [])
        results.append({"kind": "wrong_value_true_quote", "vital": entry["name"], "caught": not stored})

        forged = copy.deepcopy(output)
        forged["vitals"][index]["quote"] = f"{entry['quote']} confirmed twice by monitor"
        try:
            case_extraction.interpret(forged, info)
            caught = False
        except case_extraction.UnverifiedSourceQuote:
            caught = True
        results.append({"kind": "quote_not_in_report", "vital": entry["name"], "caught": caught})
    return results
