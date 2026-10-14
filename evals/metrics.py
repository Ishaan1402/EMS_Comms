"""Transparent metrics. Undefined rates are None, never a misleading zero."""

from itertools import combinations


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def classification_metrics(truth, predictions, labels):
    if len(truth) != len(predictions):
        raise ValueError("Mismatched label lengths")
    if any(t not in labels for t in truth):
        raise ValueError("Reference label outside metric vocabulary")
    columns = list(labels) + sorted(set(predictions) - set(labels))
    matrix = [[sum(t == label and p == column for t, p in zip(truth, predictions))
               for column in columns] for label in labels]
    classes = {}
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(truth, predictions))
        fp = sum(t != label and p == label for t, p in zip(truth, predictions))
        fn = sum(t == label and p != label for t, p in zip(truth, predictions))
        support = sum(t == label for t in truth)
        classes[label] = {"precision": ratio(tp, tp + fp), "recall": ratio(tp, support),
                          "f1": ratio(2 * tp, 2 * tp + fp + fn), "support": support,
                          "tp": tp, "fp": fp, "fn": fn,
                          "false_negative_rate": ratio(fn, support)}
    active_f1 = [v["f1"] for v in classes.values() if v["support"] and v["f1"] is not None]
    return {"n": len(truth), "accuracy": ratio(sum(t == p for t, p in zip(truth, predictions)), len(truth)),
            "macro_f1": ratio(sum(active_f1), len(active_f1)), "per_class": classes,
            "confusion_matrix": {"rows": list(labels), "columns": columns, "values": matrix}}


def pairwise_metrics(reference, predicted):
    ordered, correct, ties, correct_ties, indeterminate = 0, 0, 0, 0, 0
    for a, b in combinations(reference, 2):
        ra, rb = reference[a], reference[b]
        if not isinstance(ra, int) or not isinstance(rb, int):
            indeterminate += 1
            continue
        pa, pb = predicted.get(a), predicted.get(b)
        usable = isinstance(pa, int) and isinstance(pb, int)
        if ra == rb:
            ties += 1
            correct_ties += int(usable and pa == pb)
        else:
            ordered += 1
            correct += int(usable and ((ra < rb and pa < pb) or (ra > rb and pa > pb)))
    return {"pairwise_accuracy": ratio(correct, ordered), "correct_pairs": correct,
            "ordered_pairs": ordered, "reference_ties": ties, "correct_ties": correct_ties,
            "tie_agreement": ratio(correct_ties, ties), "indeterminate_pairs": indeterminate}


def percentile(values, quantile):
    if not values:
        return None
    values = sorted(values)
    point = (len(values) - 1) * quantile
    left, right = int(point), min(int(point) + 1, len(values) - 1)
    return values[left] + (values[right] - values[left]) * (point - left)
