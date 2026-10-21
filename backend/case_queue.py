"""A hospital's inbound queue: which open cases need attention first, and why.

The order is decided here, on the server, so every screen and export shows the same queue.
DRAFT rule until the clinician agrees to it (QUEUE_RULE_VERSION):

1. Cases that need a person now: new information the hospital hasn't acknowledged, a crew alert
   (call, change or cancellation) it hasn't acknowledged, or a processing failure (lost audio, a
   failed assessment) that nobody has fixed yet.
2. Cases with an active crew alert (stroke, STEMI, trauma, sepsis; case_alerts.py): a person's
   declared time-critical condition.
3. Earlier expected arrival (eta_at) first; a case without an ETA after those with one.
4. Earlier case first, so the order is stable.

The AI's preparation category is NOT part of the order (draft-4). It is still produced, stored,
shown ("AI suggests") and exported for evaluation, but only people's declarations, the state of
the communication and the arrival time move a patient. The one exception is AI-assisted, an
opt-in display mode a hospital's doctors can choose (ai_assisted_ordered, AI_ASSISTED_RULE_VERSION): the same
tiers 1 and 2, then the AI's category, then arrival. It is never the default, a person's call
still outranks it, and queue history keeps recording this Standard order in every mode. Software that ranks patients by its own
urgency estimate in a time-critical setting is what FDA's January 2026 clinical decision support
guidance treats as device-like; a crew's call and an arrival time are inputs a clinician can check
at a glance. draft-3 ranked by the AI's category between steps 1 and 3.

The order depends only on stored case state, never on the clock: ETAs are compared as arrival
times, so two patients whose ETAs have both passed keep their order (draft-3). It can therefore
only change when something is committed, which is when queue_history.py records it.

Cases that have arrived or are closed leave the queue; their history stays.
"""
from datetime import datetime
from typing import Optional

QUEUE_RULE_VERSION = "draft-4"
QUEUE_RULE = ("Unacknowledged information, an unacknowledged crew alert or a processing failure first, "
              "then active crew alerts, then earlier expected arrival. The AI's suggestion is shown but never "
              "changes the order.")

# The ESI level's tier in AI-assisted (a doctor's level when they set one, else the AI's suggestion).
# Not yet known (no assessment, or Cannot assess) sits between ESI 2 and ESI 3, where "not yet known"
# sat between Prepare now and Can wait in ai-assisted-1. The AI's risk score only breaks ties within a level.
AI_ASSISTED_RULE_VERSION = "ai-assisted-2"
AI_ASSISTED_RULE = ("Unacknowledged information, an unacknowledged crew alert or a processing failure first, "
              "then active crew alerts. Among cases tied on both priorities, the ESI level comes before "
              "arrival time: ESI 1, ESI 2, then not yet known (including Cannot assess), ESI 3, 4, 5. "
              "Within a level, the higher risk score first.")
ESI_RANK = {1: 0, 2: 1, None: 2, 3: 3, 4: 4, 5: 5}

NO_ALERTS = {"active": [], "unacknowledged": False}


def minutes_until(eta_at: Optional[str], now: datetime) -> Optional[int]:
    if not eta_at:
        return None
    eta = datetime.fromisoformat(eta_at.replace("Z", "+00:00"))
    return max(0, round((eta - now).total_seconds() / 60))


def crew_alerts(case: dict) -> dict:
    return case.get("crew_alerts") or NO_ALERTS


def needs_attention(case: dict) -> bool:
    return (case["processing"]["needs_review"] or not case["latest_update_acknowledged"]
            or crew_alerts(case)["unacknowledged"])


def has_crew_alert(case: dict) -> bool:
    return bool(crew_alerts(case)["active"])


def reasons(case: dict, now) -> list:
    """Why the case is where it is, in plain words for the queue: only what the rule uses."""
    why = []
    if case["processing"]["needs_review"]:
        why.extend(f"Needs review: {reason}" for reason in case["processing"]["reasons"])
    if not case["latest_update_acknowledged"]:
        why.append("New information not yet acknowledged")
    alerts = crew_alerts(case)
    if alerts["unacknowledged"]:
        why.append("Crew alert not yet acknowledged")
    why.extend(f"Crew alert: {alert['label']}" for alert in alerts["active"])
    eta = minutes_until(case["eta_at"], now)
    why.append(f"ETA {eta} min" if eta is not None else "ETA not given")
    return why


def in_queue(case: dict) -> bool:
    return case["status"] == "active" and not case["arrived_at"]


def standard_key(case: dict) -> tuple:
    """Sort key of the Standard rule (draft-4): stored case state only, never the clock."""
    return (
        0 if needs_attention(case) else 1,
        0 if has_crew_alert(case) else 1,
        case["eta_at"] is None,
        case["eta_at"] or "",  # fixed-width UTC timestamps compare in time order
        case["started_at"],
        case["id"],
    )


def ai_assisted_key(case: dict) -> tuple:
    """
    Sort key of AI-assisted, ai-assisted-2: the Standard tiers with the ESI level, then the risk score,
    between the crew alert tier and arrival time. Pure like standard_key (it reads only the case's stored fields), so
    the evaluation harness or SAS can apply it to exported cases.
    """
    needs, alert, *arrival = standard_key(case)
    return (needs, alert, ESI_RANK.get(case["esi_level"], 2), -(case["risk_score"] or 0), *arrival)


def order(cases: list, now) -> list:
    """The hospital's open cases in queue order, each with queue_position and queue_reasons."""
    queued = [case for case in cases if in_queue(case)]
    ordered = sorted(queued, key=standard_key)
    for position, case in enumerate(ordered, start=1):
        case["queue_position"] = position
        case["queue_reasons"] = reasons(case, now)
    return ordered


def counts(ordered: list) -> dict:
    return {
        "inbound": len(ordered),
        "needs_review": sum(case["processing"]["needs_review"] for case in ordered),
        "unacknowledged": sum(not case["latest_update_acknowledged"] for case in ordered),
        "crew_alerts": sum(has_crew_alert(case) for case in ordered),
        # Information only: how many are ESI 1 or 2 (it doesn't set the Standard order).
        "esi_1_2": sum(case["esi_level"] in (1, 2) for case in ordered),
    }


def ai_assisted_ordered(ordered: list) -> list:
    """
    The same queue in AI-assisted order (ai_assisted_key). Each case keeps its Standard position as
    suggested_position, so a screen can say where the Standard order would put it, and gets its
    ESI level and risk score as one more reason. Queue history still records the Standard order.
    """
    for case in ordered:
        case["suggested_position"] = case["queue_position"]
        if case["esi_level"]:
            *why, eta = case["queue_reasons"]
            source = "set by a doctor" if case.get("esi_change") else "suggested"
            risk = f", risk {case['risk_score']}/10" if case["risk_score"] else ""
            case["queue_reasons"] = [*why, f"ESI {case['esi_level']} ({source}){risk}", eta]
    result = sorted(ordered, key=ai_assisted_key)
    for position, case in enumerate(result, start=1):
        case["queue_position"] = position
    return result


def doctor_ordered(ordered: list, doctor_order: list) -> list:
    """
    The same queue in the order the hospital's doctors set by hand (Auto-order off). Cases they
    haven't placed yet, such as a new arrival, go on top in rule order so nothing new is buried.
    Each case keeps its rule position as suggested_position; queue history still records the rule.
    """
    placed = {case_id: index for index, case_id in enumerate(doctor_order)}
    for case in ordered:
        case["suggested_position"] = case["queue_position"]
    unplaced = [case for case in ordered if case["id"] not in placed]
    by_doctor = sorted((case for case in ordered if case["id"] in placed), key=lambda case: placed[case["id"]])
    result = unplaced + by_doctor
    for position, case in enumerate(result, start=1):
        case["queue_position"] = position
    return result
