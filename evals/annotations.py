"""Read the existing vertical clinician form without modifying the workbook."""

import hashlib
import re
from pathlib import Path

from .contracts import CHANGE, PREPARATION, STATUSES, blank_reference, text


def parse_doctor_rows(rows, cases):
    known = {c["case_id"]: c for c in cases}
    references, active, seen_fields = [], None, set()
    labels = {"1. Brief ED handoff": "reference_summary",
              "2. Important change?": "meaningful_change",
              "Why? What is missing?": "explanation_missing_information",
              "3. Preparation needed?": "preparation_category",
              "Review status": "status"}
    source_labels = {"Earlier report": "prior_information", "Current report": "current_transcript"}

    def finish():
        if active is not None and seen_fields != set(labels.values()) | set(source_labels.values()):
            raise ValueError(f"Incomplete/changed form layout for {active['case_id']}")

    for row in rows:
        label, value = text(row[0] if row else None), row[1] if len(row) > 1 else None
        match = re.search(r"\b(SYN\d+)\b", label) if label.startswith("Case ") else None
        if match:
            finish()
            case_id = match.group(1)
            if case_id not in known or any(r["case_id"] == case_id for r in references):
                raise ValueError(f"Unknown/duplicate case card: {case_id}")
            active, seen_fields = blank_reference(case_id), set()
            references.append(active)
            continue
        if active is None:
            continue
        first_line = label.split("\n")[0]
        field = labels.get(first_line) or source_labels.get(first_line)
        if field:
            if field in seen_fields:
                raise ValueError(f"Duplicate form field: {field}")
            seen_fields.add(field)
            if field in source_labels.values():
                if text(value) != text(known[active["case_id"]][field]):
                    raise ValueError(f"Source report changed for {active['case_id']}; version cases first")
            else:
                active[field] = text(value) or None
    finish()
    if {r["case_id"] for r in references} != set(known):
        raise ValueError("Doctor review cards do not match the supplied cases")
    for ref in references:
        for field, choices in (("meaningful_change", CHANGE), ("preparation_category", PREPARATION),
                               ("status", STATUSES)):
            if ref[field] is not None and ref[field] not in choices:
                raise ValueError(f"Invalid {field} for {ref['case_id']}: {ref[field]}")
    return references


def parse_queue_rows(rows, expected_queues, cases):
    expected = {q["queue_id"]: q for q in expected_queues}
    source = {c["case_id"]: c for c in cases}
    queues, active, case_id, seen = [], None, None, set()

    def finish_patient():
        if case_id is not None and seen != {"Earlier report", "Current report", "Preparation order"}:
            raise ValueError(f"Incomplete/changed queue patient card: {case_id}")

    for row in rows:
        label, value = text(row[0] if row else None), row[1] if len(row) > 1 else None
        group = re.match(r"Group (\d+)\b", label)
        if group:
            finish_patient()
            case_id, seen = None, set()
            queue_id = f"Q{int(group.group(1)):02d}"
            if queue_id not in expected or any(q["queue_id"] == queue_id for q in queues):
                raise ValueError(f"Unknown/duplicate queue: {queue_id}")
            if label != expected[queue_id]["form_heading"]:
                raise ValueError(f"Resource context changed for {queue_id}; version queues first")
            active = {"queue_id": queue_id, "order": {}, "explanation": None,
                      "status": "Not started", "reviewer_id": None, "rubric_version": None,
                      "approved": False}
            queues.append(active)
        elif active:
            match = re.match(r"(SYN\d+)\b", label)
            if match:
                finish_patient()
                case_id = match.group(1)
                seen = set()
                if case_id not in expected[active["queue_id"]]["case_ids"] or case_id in active["order"]:
                    raise ValueError("Unknown/duplicate patient in queue form")
                if label != f"{case_id} · ETA {source[case_id]['eta_minutes']} minutes":
                    raise ValueError(f"ETA changed for {case_id}; version cases first")
                active["order"][case_id] = None
            elif label in ("Earlier report", "Current report", "Preparation order"):
                if case_id is None or label in seen:
                    raise ValueError("Missing patient or duplicate field in queue form")
                seen.add(label)
                if label != "Preparation order":
                    field = "prior_information" if label == "Earlier report" else "current_transcript"
                    if text(value) != text(source[case_id][field]):
                        raise ValueError(f"Queue source report changed for {case_id}; version cases first")
                    continue
                val = text(value)
                if val not in ("", "1", "2", "3", "Unsure"):
                    raise ValueError(f"Invalid rank: {val}")
                active["order"][case_id] = int(val) if val in ("1", "2", "3") else val or None
            elif label.split("\n")[0] == "Why this order?":
                active["explanation"] = text(value) or None
            elif label == "Review status":
                if text(value) not in STATUSES:
                    raise ValueError("Invalid queue status")
                active["status"] = text(value)
    finish_patient()
    if {q["queue_id"] for q in queues} != set(expected):
        raise ValueError("Queue groups do not match expected queues")
    for q in queues:
        if set(q["order"]) != set(expected[q["queue_id"]]["case_ids"]):
            raise ValueError("Incomplete queue layout")
    return queues


def import_workbook(path, cases, queue_definitions):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("XLSX import needs openpyxl: pip install -r evals/requirements.txt") from exc
    path = Path(path)
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        required = ("Doctor review", "Queue exercise")
        if any(name not in workbook.sheetnames for name in required):
            raise ValueError("Expected simplified Doctor review and Queue exercise tabs")
        grids = {}
        for name in required:
            sheet = workbook[name]
            if (sheet.max_row or 0) > 10000:
                raise ValueError("Unexpected form size")
            grids[name] = []
            for cells in sheet.iter_rows(min_col=1, max_col=2):
                if len(grids[name]) >= 10000:
                    raise ValueError("Unexpected form size")
                # The progress counter is the only expected formula. Never evaluate formulas.
                if any(c.data_type == "f" for c in cells) and not (name == "Doctor review" and cells[0].row == 5):
                    raise ValueError(f"Formula in source/answer area: {name}, row {cells[0].row}")
                grids[name].append([c.value for c in cells])
        return {"kind": "clinician_reference", "dataset_version": "pilot_v1",
                "source": {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "cases": parse_doctor_rows(grids["Doctor review"], cases),
                "queues": parse_queue_rows(grids["Queue exercise"], queue_definitions, cases)}
    finally:
        workbook.close()
