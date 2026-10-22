"""Offline v2.1 TF-IDF handoff for SAS Data Maker; no generation/model/API calls."""

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .annotations_v21 import DATASET_VERSION, SOURCE_FIELDS, import_workbook, sha256_file
from .contracts import load_json, number
from .report import save_json

STATE_VERSION = "ems_tfidf_v2"
BUNDLE_VERSION = "sas_data_maker_tfidf_v1"
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# A fixed, auditable list instead of the generic English list, which removes
# clinical negation/qualifiers. Never derive stop words from held-out reports.
COMMON_STOP_WORDS = sorted(set("""
a an and are as at be been being but by did do does doing each for from had has
have having he her hers herself him himself his how i in into is it its
itself me mine my myself of on or our ours ourselves out own per s she so some
such t that the their theirs them themselves there these they this those
to too up us very was we were what where which who why will with would
you your yours yourself yourselves
crew crews ems paramedic paramedics patient patients picked report reports
reported reporting says said tells
""".split()))
LEGACY_FIXED_PARAMS = {"ngram_range": [1, 2], "min_df": 1, "max_df": .85,
                       "lowercase": True, "stop_words": None, "token_pattern": r"(?u)\b\w+\b",
                       "norm": "l2", "use_idf": True, "smooth_idf": True, "sublinear_tf": True}
FIXED_PARAMS = {**LEGACY_FIXED_PARAMS, "stop_words": COMMON_STOP_WORDS}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def input_text(source):
    """Deliberate allowlist: no identifiers, reference text, answers, or reviewer metadata."""
    if not isinstance(source.get("current_transcript"), str) or not source["current_transcript"].strip():
        raise ValueError("Missing current transcript")
    prior = source.get("prior_information", "")
    demographics = source.get("age_sex_weight", "")
    if not isinstance(prior, str) or not isinstance(demographics, str):
        raise ValueError("Invalid source text fields")
    return (f"Reported demographics at latest update: {demographics}\n"
            f"Earlier EMS report: {prior}\nLatest EMS report: {source['current_transcript']}")


def grouped_split(cases, references, *, seed=20261021, test_fraction=.3, test_ids=None, group_map=None):
    """Choose a reproducible development split before learning any vocabulary/IDF.

    Default groups are original case IDs. Explicit maps keep known shared scenario
    families together. Candidate selection uses only labels/group membership, not
    text features or downstream scores. No automatic performance-based re-split.
    """
    from sklearn.model_selection import GroupShuffleSplit
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed > 2**32 - 1:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if not number(test_fraction) or not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between zero and one")
    ordered = sorted(cases, key=lambda c: c["case_id"])
    ids = [c["case_id"] for c in ordered]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Case IDs must be nonempty and unique")
    targets = {r["case_id"]: r["esi_level"] for r in references}
    if set(targets) != set(ids) or len(references) != len(targets):
        raise ValueError("ESI references must match case IDs exactly")
    if any(not number(y, integer=True, minimum=1) or y > 5 for y in targets.values()):
        raise ValueError("ESI labels must be numeric 1–5")
    if group_map is None:
        group_map = {cid: cid for cid in ids}
    if not isinstance(group_map, dict) or set(group_map) != set(ids) or any(
            not isinstance(g, str) or not ID_PATTERN.fullmatch(g) for g in group_map.values()):
        raise ValueError("Group map must assign every case exactly one safe scenario-family ID")
    groups = [group_map[cid] for cid in ids]
    if len(set(groups)) < 2:
        raise ValueError("At least two independent groups are needed")
    labels = [targets[cid] for cid in ids]
    all_labels = set(labels)
    if test_ids is not None:
        if not test_ids or len(set(test_ids)) != len(test_ids) or set(test_ids) - set(ids):
            raise ValueError("test_ids must be unique known case IDs")
        test_indices = [i for i, cid in enumerate(ids) if cid in test_ids]
        train_indices = [i for i, cid in enumerate(ids) if cid not in test_ids]
        candidates = [(train_indices, test_indices)]
    else:
        candidates = GroupShuffleSplit(n_splits=256, test_size=test_fraction,
                                       random_state=seed).split(ids, labels, groups)
    best = None
    for train, test in candidates:
        if not len(train) or not len(test):
            continue
        if {groups[i] for i in train} & {groups[i] for i in test}:
            if test_ids is not None:
                raise ValueError("Explicit split puts the same scenario family in train and test")
            continue
        if {labels[i] for i in train} != all_labels:
            continue
        missing_test_classes = len(all_labels - {labels[i] for i in test})
        # Favor class coverage, then requested test size. Keep the first tied seeded draw.
        score = (missing_test_classes, abs(len(test) / len(ids) - test_fraction))
        if best is None or score < best[0]:
            best = (score, list(train), list(test))
    if best is None:
        raise ValueError("No valid grouped split retaining every observed ESI class in training; provide more groups or explicit test IDs")
    train_ids = [ids[i] for i in best[1]]
    held_ids = [ids[i] for i in best[2]]
    return {"train_ids": train_ids, "test_ids": held_ids, "groups": group_map, "seed": seed,
            "requested_test_fraction": test_fraction, "actual_test_fraction": len(held_ids) / len(ids),
            "grouping": "explicit_scenario_families" if any(cid != g for cid, g in group_map.items()) else "original_case_id",
            "training_class_counts": dict(Counter(str(targets[cid]) for cid in train_ids)),
            "test_class_counts": dict(Counter(str(targets[cid]) for cid in held_ids)),
            "missing_test_classes": sorted(all_labels - {targets[cid] for cid in held_ids}),
            "purpose": "development_feasibility_not_independent_clinical_validation"}


def fit_features(training, testing, *, max_features=30):
    import sklearn
    from sklearn.feature_extraction.text import TfidfVectorizer
    if not isinstance(max_features, int) or isinstance(max_features, bool) or not 1 <= max_features <= 256:
        raise ValueError("max_features must be an integer from 1 to 256 for this small dense export")
    params = {**FIXED_PARAMS, "max_features": max_features, "ngram_range": (1, 2)}
    vectorizer = TfidfVectorizer(**params)
    train_matrix = vectorizer.fit_transform([input_text(c) for c in training])
    test_matrix = vectorizer.transform([input_text(c) for c in testing])
    terms = vectorizer.get_feature_names_out().tolist()
    columns = [f"tfidf_{i:04d}" for i in range(len(terms))]
    state = {"kind": STATE_VERSION, "dataset_version": DATASET_VERSION, "sklearn_version": sklearn.__version__,
             "parameters": {**params, "ngram_range": list(params["ngram_range"])},
             "input_recipe": "reported_demographics_then_earlier_then_latest_v1",
             "input_fields": ["age_sex_weight", "prior_information", "current_transcript"],
             "vocabulary": {term: i for i, term in enumerate(terms)}, "idf": vectorizer.idf_.tolist(),
             "feature_columns": columns, "fit_case_ids": [c["case_id"] for c in training]}
    state["state_sha256"] = canonical_hash(state)
    dictionary = [{"feature": col, "term": term, "idf": float(vectorizer.idf_[i])}
                  for i, (col, term) in enumerate(zip(columns, terms))]
    return train_matrix.toarray(), test_matrix.toarray(), state, dictionary


def transform_records(cases, state):
    """Restore a saved vectorizer from plain JSON, never pickle/untrusted executable code."""
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    if (not isinstance(state, dict) or state.get("kind") not in (STATE_VERSION, "ems_tfidf_v1")
            or state.get("dataset_version") != DATASET_VERSION
            or state.get("input_recipe") != "reported_demographics_then_earlier_then_latest_v1"
            or state.get("input_fields") != ["age_sex_weight", "prior_information", "current_transcript"]
            or state.get("state_sha256") != canonical_hash({k: v for k, v in state.items() if k != "state_sha256"})):
        raise ValueError("Invalid vectorizer state/version/hash")
    vocabulary, idf, columns = state["vocabulary"], state["idf"], state["feature_columns"]
    n = len(columns)
    if (not n or n > 256 or not isinstance(vocabulary, dict)
            or any(not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool) for k, v in vocabulary.items())
            or sorted(vocabulary.values()) != list(range(n)) or len(vocabulary) != n or len(idf) != n
            or columns != [f"tfidf_{i:04d}" for i in range(n)]
            or any(not number(value, minimum=1) for value in idf)):
        raise ValueError("Invalid vectorizer vocabulary/IDF dimensions")
    saved_params = state["parameters"]
    expected_params = LEGACY_FIXED_PARAMS if state["kind"] == "ems_tfidf_v1" else FIXED_PARAMS
    if (not isinstance(saved_params, dict) or set(saved_params) != set(expected_params) | {"max_features"}
            or any(saved_params.get(k) != v for k, v in expected_params.items())
            or not number(saved_params.get("max_features"), integer=True, minimum=1)
            or saved_params["max_features"] > 256):
        raise ValueError("Vectorizer parameters differ from the frozen preparation contract")
    params = {**state["parameters"], "ngram_range": tuple(state["parameters"]["ngram_range"])}
    vectorizer = TfidfVectorizer(**params, vocabulary=vocabulary)
    vectorizer.idf_ = np.asarray(idf)
    return vectorizer.transform([input_text(c) for c in cases]).toarray()


def csv_file(path, rows, fields):
    """Escape text that spreadsheet applications could otherwise interpret as formulas."""
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value
    with Path(path).open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: safe(row[key]) for key in fields})


def feature_rows(matrix, columns, labels=None):
    rows = []
    for i, values in enumerate(matrix):
        row = {key: float(value) for key, value in zip(columns, values)}
        if labels is not None:
            row["esi_level"] = int(labels[i])
        rows.append(row)
    return rows


HANDOFF_GUIDE = """# For the SAS Data Maker colleague

Upload only train_features.csv. Each row is one original training case, not an
independent observation for every prior/latest report. All columns prefixed
tfidf_ are continuous text weights, not clinical measurements. esi_level is the
categorical target (1 is most urgent). Do not treat ESI as a continuous feature.
Generate features and ESI together so their joint relationship is represented.
Do not synthesize feature columns and attach labels independently afterward.

feature_dictionary.csv maps columns to words/phrases, with training-only IDF.
Common function words, pronouns and report boilerplate are filtered before
selection. Negations/clinical qualifiers (for example no, not, without, before,
after, now, unable) and numbers are retained. These are still text weights, not
clinical measurements or a guarantee of meaningful temporal interpretation.
schema.json defines exact column order/types/ranges/observed target classes.
manifest.json identifies this training bundle and file checksums. No test rows,
source transcripts, reference summaries, reviewer identities, patient IDs or
other annotation targets are in this package. Do not request test data to fit,
tune, assess or choose the generator. Do not add held-out reports or vocabulary.

This is a tiny, provisional single-reviewer development pilot, not clinical gold.
Many features relative to very few rows may make synthesis ineffective or fail
the product's minimum-row requirements. Do not duplicate rows merely to bypass
minimum counts, invent ESI 4/5 labels, or claim synthetic rows are expert reviews.
If generation fails or produces near-copies, report that result honestly.

Return:
1. synthetic_train_features.csv with EXACTLY the same columns and order as the
   input, numeric finite TF-IDF weights in [0,1] and only observed ESI classes.
   No new IDs, targets, index columns, renamed features or silently rounded labels.
2. generation_metadata.json containing source_bundle_id from manifest.json,
   generator/model version, configuration, random seed (or not available), input
   and output row counts, ESI class counts, target conditioning if used, and any
   constraints, normalization, filtering, resampling or quality checks applied.
3. Data Maker's quality report, including any warnings and duplicate/near-copy
   checks. Exact feature sums need not be one: TF-IDF uses L2, not L1 normalization.
   Original nonzero vectors have L2 norm one. Check/report how generated vectors
   differ rather than silently normalizing them after generation.

Keep the synthetic table labeled synthetic. Do not split it to manufacture an
independent clinical test set. The owner retains the fixed original test rows
and will compare original-only versus augmented training on that SAME test set.
No generation or upload has been performed by the preparation pipeline.
"""


def prepare(workbook, directory, *, confirm_synthetic=False, acknowledge_provisional=False,
            max_features=30, seed=20261021, test_fraction=.3, test_ids=None, group_map=None):
    if not confirm_synthetic or not acknowledge_provisional:
        raise ValueError("Preparation requires --confirm-synthetic and --acknowledge-provisional-labels")
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Output directory already exists; choose a new preparation run")
    imported = import_workbook(workbook)
    if imported["issues"]:
        detail = "; ".join(f"{i['case_id']}/{i['field']}: {i['problem']}" for i in imported["issues"])
        raise ValueError(f"Resolve import issues before exporting: {detail}")
    cases, references = imported["cases"], imported["references"]
    split = grouped_split(cases, references, seed=seed, test_fraction=test_fraction,
                          test_ids=test_ids, group_map=group_map)
    by_id = {c["case_id"]: c for c in cases}
    labels = {r["case_id"]: r["esi_level"] for r in references}
    train = [by_id[cid] for cid in split["train_ids"]]
    test = [by_id[cid] for cid in split["test_ids"]]
    train_matrix, test_matrix, state, dictionary = fit_features(train, test, max_features=max_features)
    columns = state["feature_columns"]
    train_rows = feature_rows(train_matrix, columns, [labels[c["case_id"]] for c in train])
    test_rows = feature_rows(test_matrix, columns, [labels[c["case_id"]] for c in test])
    # All validation/feature fitting happens before creating any output directory.
    directory.mkdir(parents=True, exist_ok=False)
    private, share = directory / "private", directory / "share_with_colleague"
    private.mkdir()
    share.mkdir()
    save_json(private / "annotation_import.json", imported)
    save_json(private / "source_inputs.json", {"dataset_version": DATASET_VERSION, "cases": cases})
    save_json(private / "split_manifest.json", split)
    save_json(private / "vectorizer.json", state)
    save_json(private / "row_mapping.json", {"train": split["train_ids"], "test": split["test_ids"],
              "note": "CSV data rows in this order; identifiers are never synthesis features"})
    csv_file(private / "test_features.csv", test_rows, columns + ["esi_level"])
    csv_file(share / "train_features.csv", train_rows, columns + ["esi_level"])
    csv_file(share / "feature_dictionary.csv", dictionary, ["feature", "term", "idf"])
    schema = {"kind": BUNDLE_VERSION, "dataset_version": DATASET_VERSION,
              "columns": [{"name": col, "role": "feature", "type": "continuous", "minimum": 0, "maximum": 1} for col in columns]
                         + [{"name": "esi_level", "role": "target", "type": "categorical", "storage": "integer",
                             "allowed_values": sorted({row["esi_level"] for row in train_rows})}],
              "target": "esi_level", "joint_synthesis_required": True,
              "labels": "provisional_single_reviewer_not_clinical_validation",
              "vectorizer_state_sha256": state["state_sha256"]}
    save_json(share / "schema.json", schema)
    (share / "README.md").write_text(HANDOFF_GUIDE, encoding="utf-8")
    share_hashes = {p.name: sha256_file(p) for p in sorted(share.iterdir())}
    manifest = {"kind": BUNDLE_VERSION, "dataset_version": DATASET_VERSION,
                "source_bundle_id": canonical_hash(share_hashes), "files": share_hashes,
                "source_workbook_sha256": imported["source_sha256"],
                "vectorizer_state_sha256": state["state_sha256"], "training_rows": len(train),
                "features": len(columns), "training_esi_counts": split["training_class_counts"],
                "label_status": "provisional_single_reviewer", "purpose": split["purpose"],
                "seed": seed, "max_features": max_features,
                "text_preprocessing": {"version": STATE_VERSION, "stop_words": COMMON_STOP_WORDS},
                "notes": ["Upload only train_features.csv. Other files document the contract, not extra training data.",
                          "No SAS service, generator or model API was called. Test data is excluded from this package."]}
    save_json(share / "manifest.json", manifest)
    audit = {"kind": "tfidf_preparation_audit_v1", "dataset_version": DATASET_VERSION,
             "source_workbook_sha256": imported["source_sha256"], "source_bundle_id": manifest["source_bundle_id"],
             "training_rows": len(train), "test_rows": len(test), "features": len(columns),
             "training_class_counts": split["training_class_counts"], "test_class_counts": split["test_class_counts"],
             "absent_esi_levels": imported["absent_esi_levels"], "missing_test_classes": split["missing_test_classes"],
             "vectorizer_state_sha256": state["state_sha256"],
             "zero_feature_training_rows": [c["case_id"] for c, v in zip(train, train_matrix) if not v.any()],
             "zero_feature_test_rows": [c["case_id"] for c, v in zip(test, test_matrix) if not v.any()],
             "warnings": imported["warnings"] + [
                 "Case ID grouping does not discover related clinical scenario families; provide --group-map when known.",
                 "Bag-of-words features do not reliably encode temporal order, clinical negation, or treatment response.",
                 "Feature count is an engineering cap, not evidence that ten cases suffice for synthesis or ML.",
                 "Do not use held-out scores to select generator settings, prompts or feature limits.",
                 "No patients, synthetic rows, ML classifiers, LLM outputs or clinical performance claims were generated."],
             "local_artifact_sha256": {p.name: sha256_file(p) for p in sorted(private.iterdir())}}
    save_json(directory / "preparation_audit.json", audit)
    with ZipFile(directory / "colleague_handoff.zip", "x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(share.iterdir()):
            archive.write(path, arcname=path.name)
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare training-only TF-IDF tabular data for a SAS Data Maker colleague")
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import", help="Read/audit revised ESI annotations; no approval or feature fitting")
    imp.add_argument("--workbook", type=Path, required=True)
    imp.add_argument("--out", type=Path, required=True)
    prep = commands.add_parser("prepare", help="Create training-only colleague ZIP and separate local development test data")
    prep.add_argument("--workbook", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--confirm-synthetic", action="store_true")
    prep.add_argument("--acknowledge-provisional-labels", action="store_true")
    prep.add_argument("--max-features", type=int, default=30)
    prep.add_argument("--seed", type=int, default=20261021)
    prep.add_argument("--test-fraction", type=float, default=.3)
    prep.add_argument("--test-ids", help="Optional comma-separated original case IDs; must respect groups")
    prep.add_argument("--group-map", type=Path, help="Optional JSON object mapping every case to its scenario-family ID")
    transform = commands.add_parser("transform", help="Apply frozen JSON vectorizer to source-only reports; no refit")
    transform.add_argument("--vectorizer", type=Path, required=True)
    transform.add_argument("--cases", type=Path, required=True, help="JSON with dataset_version and source-only cases")
    transform.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError("Output directory already exists; choose a new run")
        if args.command == "import":
            imported = import_workbook(args.workbook)
            args.out.mkdir(parents=True, exist_ok=False)
            save_json(args.out / "annotation_import.json", imported)
            print(f"Imported {len(imported['cases'])} v2.1 cases; {len(imported['issues'])} import issues. No approvals inferred.")
        elif args.command == "prepare":
            audit = prepare(args.workbook, args.out, confirm_synthetic=args.confirm_synthetic,
                            acknowledge_provisional=args.acknowledge_provisional_labels, max_features=args.max_features,
                            seed=args.seed, test_fraction=args.test_fraction,
                            test_ids=args.test_ids.split(",") if args.test_ids else None,
                            group_map=load_json(args.group_map) if args.group_map else None)
            print(f"Prepared {audit['training_rows']} training rows, {audit['test_rows']} local test rows, {audit['features']} TF-IDF features.")
            print("Send ONLY colleague_handoff.zip. Keep private/ and preparation_audit.json local.")
            print("DEVELOPMENT FEASIBILITY ONLY. Provisional labels; no clinical validation, synthesis, classifier training or API calls.")
        else:
            state, data = load_json(args.vectorizer), load_json(args.cases)
            if data.get("dataset_version") != DATASET_VERSION or not isinstance(data.get("cases"), list) or not data["cases"]:
                raise ValueError("Expected versioned source-only cases")
            cases = data["cases"]
            ids = [c.get("case_id") for c in cases]
            if (any(not isinstance(cid, str) or not ID_PATTERN.fullmatch(cid) for cid in ids)
                    or len(set(ids)) != len(ids) or any(set(c) - set(SOURCE_FIELDS) for c in cases)):
                raise ValueError("Source-only cases require unique safe case IDs and no annotation fields")
            matrix = transform_records(cases, state)
            args.out.mkdir(parents=True, exist_ok=False)
            csv_file(args.out / "features.csv", feature_rows(matrix, state["feature_columns"]), state["feature_columns"])
            save_json(args.out / "row_mapping.json", {"case_ids": ids, "vectorizer_state_sha256": state["state_sha256"]})
            print(f"Transformed {len(cases)} reports with the frozen training vectorizer; no refit.")
        print(f"Created {args.out.resolve()}")
        return 0
    except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
        print(f"TF-IDF preparation error: {exc}", file=sys.stderr)
        if isinstance(exc, ImportError):
            print("Install optional dependencies: python -m pip install -r evals/requirements-ml.txt", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
