"""Portable HTML dashboard and flat tables for Python/SAS. No external assets."""

import csv
import html
import json
from pathlib import Path


LABELS = {
    "config_id": "Configuration", "change_precision": "Change Precision",
    "change_recall": "Change Recall", "change_f1": "Change F1",
    "preparation_accuracy": "Preparation Accuracy", "preparation_macro_f1": "Preparation Macro-F1",
    "prepare_now_false_negative_rate": "Prepare-now False Negative Rate",
    "pairwise_accuracy": "Pairwise Accuracy", "usable_response_rate": "Usable Response Rate",
    "expected_responses": "Expected Responses", "schema_validation_rate": "Schema Validation Rate",
    "error_rate": "Error Rate", "latency_median_ms": "Median Latency (ms)",
    "latency_p95_ms": "P95 Latency (ms)", "observed_cost_usd": "Reported Case Cost (USD)",
    "content_recall": "Content Recall", "factual_precision": "Factual Precision",
    "unsupported_claim_rate": "Unsupported Claim Rate", "summary_review_coverage": "Summary Review Coverage",
}


def fmt(value):
    if value is None:
        return "N/A"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def comparison_rows(report):
    rows = []
    for result in report["results"]:
        change, prep, summary = result["change"], result["preparation"], result["summary"]
        urgent = prep["per_class"]["Prepare now"]
        rows.append({"mode": report["mode"], "config_id": result["config"]["config_id"],
                     "independent_cases": result["independent_reference_cases"],
                     "expected_responses": result["expected_responses"],
                     "usable_response_rate": result["usable_response_rate"],
                     "schema_validation_rate": result["schema_validation_rate"],
                     "error_rate": result["error_rate"],
                     "change_accuracy": change["accuracy"], "change_precision": change["per_class"]["Yes"]["precision"],
                     "change_recall": change["per_class"]["Yes"]["recall"], "change_f1": change["per_class"]["Yes"]["f1"],
                     "preparation_accuracy": prep["accuracy"], "preparation_macro_f1": prep["macro_f1"],
                     "prepare_now_false_negative_rate": urgent["false_negative_rate"],
                     "prepare_now_support": urgent["support"], "pairwise_accuracy": result["queue"]["pairwise_accuracy"],
                     "ordered_pairs": result["queue"]["ordered_pairs"], "content_recall": summary["content_recall"],
                     "factual_precision": summary["factual_precision"], "unsupported_claim_rate": summary["unsupported_claim_rate"],
                     "summary_review_coverage": summary["review_coverage"],
                     "latency_median_ms": result["latency_median_ms"], "latency_p95_ms": result["latency_p95_ms"],
                     "observed_cost_usd": result["observed_cost_usd"]})
    return rows


def table(headers, rows):
    escape = lambda v: html.escape(fmt(v))
    return ("<table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in rows)
            + "</tbody></table>")


def render_report(report):
    rows = comparison_rows(report)
    headline = ["config_id", "change_precision", "change_recall", "change_f1", "preparation_accuracy",
                "preparation_macro_f1", "prepare_now_false_negative_rate", "pairwise_accuracy", "usable_response_rate"]
    parts = ["<!doctype html><html><head><meta charset='utf-8'><title>EMS evaluation</title>",
             "<style>body{font:16px system-ui;margin:32px;max-width:1400px;color:#222}h1,h2,h3{line-height:1.2}"
             "table{border-collapse:collapse;max-width:100%;display:block;overflow:auto;margin:18px 0}"
             "th,td{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}th{background:#f2f2f2}"
             ".notice{padding:16px;background:#fff3cd;border:1px solid #e0cb78}"
             ".bar{height:15px;background:#326b9b;display:block;max-width:100%}.metric{margin:12px 0}"
             "details{margin:12px 0}li{margin:6px 0}</style></head><body>",
             f"<h1>EMS evaluation</h1><div class='notice'><strong>{html.escape(report['mode'])}</strong><br>",
             f"{'Mock references (artificial approvals)' if report['mode'].startswith('MOCK') else 'Approved clinician reference cases'}: "
             f"{report['approved_reference_cases']} / {report['reference_cases_total']}. "
             "N/A means unavailable, not zero. Repeats are not independent patients.</div>",
             "<h2>Configuration comparison</h2><p>Scroll the table horizontally on smaller screens. Rates use 0–1.</p>",
             table([LABELS.get(k, k) for k in headline], [[row[k] for k in headline] for row in rows])]
    if not report["approved_reference_cases"]:
        parts.append("<p><strong>No case quality scores yet: approve completed clinician case references first. Queue scoring has its own approval gate.</strong></p>")
    for result, row in zip(report["results"], rows):
        parts.append(f"<h2>{html.escape(row['config_id'])}</h2>")
        for key in ("change_precision", "change_recall", "change_f1", "preparation_accuracy", "pairwise_accuracy"):
            value = row[key]
            width = 0 if value is None else 300 * value
            parts.append(f"<div class='metric'>{html.escape(LABELS.get(key, key))}: {fmt(value)} "
                         f"<span class='bar' style='width:{width:.1f}px'></span></div>")
        parts.append("<h3>Confusion matrices</h3><p>Rows = reference; columns = prediction. Failures remain visible.</p>")
        for task in ("change", "preparation"):
            cm = result[task]["confusion_matrix"]
            parts.extend([f"<h3>{task.title()}</h3>", table(["Reference / prediction"] + cm["columns"],
                         [[label] + values for label, values in zip(cm["rows"], cm["values"])])])
        parts.append("<h3>Reliability, cost and summary review</h3>")
        keys = ["expected_responses", "usable_response_rate", "schema_validation_rate", "error_rate",
                "latency_median_ms", "latency_p95_ms", "observed_cost_usd", "content_recall", "factual_precision",
                "unsupported_claim_rate", "summary_review_coverage"]
        parts.append(table(["Metric", "Value"], [[LABELS.get(k, k), row[k]] for k in keys]))
        parts.append(f"<p>Response status counts: {html.escape(str(result['status_counts']))}</p>")
        parts.append("<h3>Case review</h3>")
        for case in result["case_details"]:
            parts.append("<details><summary>" + html.escape(f"{case['case_id']} / repeat {case['repeat_id']} / {case['status']}")
                         + "</summary>" + table(["Field", "Value"], [[k, v] for k, v in case.items()]) + "</details>")
    parts.extend(["<h2>Excluded references</h2>", table(["Case", "Reason"], [[k, "; ".join(v)] for k, v in report["excluded_references"].items()]),
                  "<h2>Definitions and limitations</h2><ul>",
                  *[f"<li>{html.escape(item)}</li>" for item in report["limitations"]], "</ul></body></html>"])
    return "".join(parts)


def save_json(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        # These exports contain numeric/controlled comparison fields; None is an empty field.
        writer.writerows(rows)


def save_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / "metrics.json", report)
    (directory / "dashboard.html").write_text(render_report(report), encoding="utf-8")
    write_csv(directory / "comparison.csv", comparison_rows(report))
    # Leave free-text summaries in JSON/HTML to avoid spreadsheet-formula injection in CSV.
    detail = [{k: v for k, v in case.items() if k not in ("summary", "reference_summary", "schema_errors")}
              for r in report["results"] for case in r["case_details"]]
    write_csv(directory / "case_results.csv", detail)
