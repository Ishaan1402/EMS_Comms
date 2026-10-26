"""Agreement statistics, standard library only, so anyone can recompute them.

Weighted kappa follows SAS PROC FREQ (AGREE): Cicchetti-Allison (linear) and Fleiss-Cohen
(quadratic) weights over the fixed levels 1-5, and the same asymptotic standard error, so a
SAS run on results/esi_pairs.csv reproduces these numbers (sas/eir_ai_agreement.sas).
"""
import math
import random

LEVELS = (1, 2, 3, 4, 5)
Z95 = 1.959963984540054


def wilson(k: int, n: int) -> dict:
    """A proportion with its 95% Wilson score interval (sensible at small n and near 0 or 1)."""
    if not n:
        return {"k": k, "n": n, "value": None, "low": None, "high": None}
    p = k / n
    centre = (p + Z95 ** 2 / (2 * n)) / (1 + Z95 ** 2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95 ** 2 / (4 * n * n)) / (1 + Z95 ** 2 / n)
    return {"k": k, "n": n, "value": p, "low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def table(pairs, levels=LEVELS):
    """Counts[i][j]: reference level i (rows), prediction level j (columns)."""
    index = {level: i for i, level in enumerate(levels)}
    counts = [[0] * len(levels) for _ in levels]
    for reference, predicted in pairs:
        counts[index[reference]][index[predicted]] += 1
    return counts


def weights(kind: str, levels=LEVELS):
    span = levels[-1] - levels[0]
    if kind == "none":
        return [[1.0 if a == b else 0.0 for b in levels] for a in levels]
    if kind == "linear":  # SAS WT=CA
        return [[1 - abs(a - b) / span for b in levels] for a in levels]
    if kind == "quadratic":  # SAS WT=FC
        return [[1 - (a - b) ** 2 / span ** 2 for b in levels] for a in levels]
    raise ValueError(kind)


def kappa(pairs, kind="quadratic", levels=LEVELS) -> dict:
    """
    Cohen's kappa (kind 'none') or weighted kappa, with SAS's asymptotic standard error and the
    95% Wald interval SAS prints. None when undefined (no pairs, or all expected agreement).
    """
    pairs = list(pairs)
    n = len(pairs)
    if not n:
        return {"value": None, "ase": None, "low": None, "high": None, "n": 0}
    counts = table(pairs, levels)
    w = weights(kind, levels)
    k = len(levels)
    p = [[counts[i][j] / n for j in range(k)] for i in range(k)]
    rows = [sum(p[i]) for i in range(k)]
    cols = [sum(p[i][j] for i in range(k)) for j in range(k)]
    observed = sum(w[i][j] * p[i][j] for i in range(k) for j in range(k))
    expected = sum(w[i][j] * rows[i] * cols[j] for i in range(k) for j in range(k))
    if expected >= 1:
        return {"value": None, "ase": None, "low": None, "high": None, "n": n}
    value = (observed - expected) / (1 - expected)
    w_row = [sum(cols[j] * w[i][j] for j in range(k)) for i in range(k)]
    w_col = [sum(rows[i] * w[i][j] for i in range(k)) for j in range(k)]
    total = sum(p[i][j] * (w[i][j] - (w_row[i] + w_col[j]) * (1 - value)) ** 2 for i in range(k) for j in range(k))
    variance = (total - (value - expected * (1 - value)) ** 2) / ((1 - expected) ** 2 * n)
    ase = math.sqrt(max(variance, 0.0))
    return {"value": value, "ase": ase, "low": value - Z95 * ase, "high": value + Z95 * ase, "n": n}


def bootstrap(pairs, statistic, resamples=2000, seed=20261026) -> dict:
    """Percentile 95% interval from resampling cases with replacement (fixed seed, reproducible)."""
    pairs = list(pairs)
    if not pairs:
        return {"low": None, "high": None, "resamples": 0, "undefined": 0}
    rng = random.Random(seed)
    values, undefined = [], 0
    for _ in range(resamples):
        value = statistic([pairs[rng.randrange(len(pairs))] for _ in pairs])
        if value is None:
            undefined += 1
        else:
            values.append(value)
    values.sort()
    if not values:
        return {"low": None, "high": None, "resamples": resamples, "undefined": undefined}

    def at(q):
        position = q * (len(values) - 1)
        low = math.floor(position)
        return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)
    return {"low": at(0.025), "high": at(0.975), "resamples": resamples, "undefined": undefined}


def agreement(pairs, levels=LEVELS) -> dict:
    """
    Everything reported for one set of (reference, predicted) levels. Lower number = more urgent,
    so predicted > reference is under-triage (less urgent than the reference).
    """
    pairs = list(pairs)
    n = len(pairs)
    exact = sum(r == p for r, p in pairs)
    within_one = sum(abs(r - p) <= 1 for r, p in pairs)
    under = sum(p > r for r, p in pairs)
    over = sum(p < r for r, p in pairs)
    high = [(r, p) for r, p in pairs if r <= 2]
    low = [(r, p) for r, p in pairs if r >= 3]
    critical = [(r, p) for r, p in pairs if r == 1]
    quadratic = kappa(pairs, "quadratic", levels)
    return {
        "n": n,
        "exact": wilson(exact, n),
        "within_one": wilson(within_one, n),
        "under_triage": wilson(under, n),
        "over_triage": wilson(over, n),
        "under_by_2_or_more": wilson(sum(p - r >= 2 for r, p in pairs), n),
        # Reference 1-2 (immediate / emergent): how many the AI also put at 1-2.
        "high_acuity_sensitivity": wilson(sum(p <= 2 for _, p in high), len(high)),
        "high_acuity_missed_to_3_plus": wilson(sum(p >= 3 for _, p in high), len(high)),
        "low_acuity_specificity": wilson(sum(p >= 3 for _, p in low), len(low)),
        "level_1_caught_at_1_2": wilson(sum(p <= 2 for _, p in critical), len(critical)),
        "kappa_unweighted": kappa(pairs, "none", levels),
        "kappa_linear": kappa(pairs, "linear", levels),
        "kappa_quadratic": quadratic,
        "kappa_quadratic_bootstrap": bootstrap(pairs, lambda s: kappa(s, "quadratic", levels)["value"]),
        "mean_signed_error": sum(p - r for r, p in pairs) / n if n else None,
        "confusion": {"rows_reference": list(levels), "columns_predicted": list(levels), "counts": table(pairs, levels)},
    }


def percentile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    position = q * (len(values) - 1)
    low = math.floor(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)
