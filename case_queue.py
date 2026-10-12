"""A hospital's inbound queue: which open cases need attention first, and why.

The order is decided here, on the server, so every screen and export shows the same queue.
DRAFT rule until the clinician agrees to it (QUEUE_RULE_VERSION):

1. Cases that need a person now: new information the hospital hasn't acknowledged, or a
   processing failure (lost audio, a failed assessment) that nobody has fixed yet.
2. Preparation category: Prepare now, then not yet known (no assessment, or Cannot assess),
   then Can wait, then Routine. Unknown sits above Can wait because unknown is not low risk.
   A "Cannot assess" the hospital has acknowledged stays here, with the unknowns: the clinician
   has looked, so it no longer outranks a known Prepare now (draft-2).
3. Earlier expected arrival (eta_at) first; a case without an ETA after those with one.
4. Earlier case first, so the order is stable.

The order depends only on stored case state, never on the clock: ETAs are compared as arrival
times, so two patients whose ETAs have both passed keep their order (draft-3; draft-2 compared
minutes left, which stop at zero). It can therefore only change when something is committed,
which is when queue_history.py records it.

Cases that have arrived or are closed leave the queue; their history stays.
"""
from datetime import datetime
from typing import Optional

QUEUE_RULE_VERSION = "draft-3"
QUEUE_RULE = ("Draft rule: unacknowledged information or a processing failure first, then Prepare now, "
              "not yet known (including acknowledged Cannot assess), Can wait, Routine, then earlier expected arrival.")

CATEGORY_RANK = {"Prepare now": 0, None: 1, "Cannot assess": 1, "Can wait": 2, "Routine": 3}


def minutes_until(eta_at: Optional[str], now: datetime) -> Optional[int]:
    if not eta_at:
        return None
    eta = datetime.fromisoformat(eta_at.replace("Z", "+00:00"))
    return max(0, round((eta - now).total_seconds() / 60))


def needs_attention(case: dict) -> bool:
    return case["processing"]["needs_review"] or not case["latest_update_acknowledged"]


def reasons(case: dict, now) -> list:
    """Why the case is where it is, in plain words for the queue."""
    why = []
    if case["processing"]["needs_review"]:
        why.extend(f"Needs review: {reason}" for reason in case["processing"]["reasons"])
    if not case["latest_update_acknowledged"]:
        why.append("New information not yet acknowledged")
    category = case["preparation_category"]
    why.append(category if category else "Not yet assessed")
    eta = minutes_until(case["eta_at"], now)
    if eta is not None:
        why.append(f"ETA {eta} min")
    return why


def in_queue(case: dict) -> bool:
    return case["status"] == "active" and not case["arrived_at"]


def order(cases: list, now) -> list:
    """The hospital's open cases in queue order, each with queue_position and queue_reasons."""
    queued = [case for case in cases if in_queue(case)]

    def key(case):
        return (
            0 if needs_attention(case) else 1,
            CATEGORY_RANK.get(case["preparation_category"], 1),
            case["eta_at"] is None,
            case["eta_at"] or "",  # fixed-width UTC timestamps compare in time order
            case["started_at"],
            case["id"],
        )

    ordered = sorted(queued, key=key)
    for position, case in enumerate(ordered, start=1):
        case["queue_position"] = position
        case["queue_reasons"] = reasons(case, now)
    return ordered


def counts(ordered: list) -> dict:
    return {
        "inbound": len(ordered),
        "needs_review": sum(case["processing"]["needs_review"] for case in ordered),
        "unacknowledged": sum(not case["latest_update_acknowledged"] for case in ordered),
        "prepare_now": sum(case["preparation_category"] == "Prepare now" for case in ordered),
    }
