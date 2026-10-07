"""Build past-only features, train fixed pointwise models, and evaluate validation only."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .audit_raw import ROOT, sha256
from .ranking_evaluation import HEURISTICS, evaluate_validation, error_analysis, select_and_compare
from .ranking_features import SCHEMA_PATH, generate_features, load_schema, make_examples, verify_temporal_separation
from .ranking_models import CONFIG_PATH, fit_model, load_artifact, load_model_config, save_artifact, score_model
from .retrieval import DEFAULT_POLICY, RetrievalIndex, interleave, load_policy
from .temporal_snapshots import REQUIRED_COLUMNS, cleaning_hashes, monthly_cutoffs
from .training_baselines import CANDIDATE_SCHEMA, arrow_rows


def dump_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def validation_candidates(paid, queries, catalog, policy):
    """Reuse the unchanged V1 sources and merge, without accepting label data."""
    cutoff = pd.Timestamp(queries.cutoff.iloc[0])
    index = RetrievalIndex(paid, cutoff, catalog.sku, policy)
    rows = []
    for query in queries.sort_values("customer_id").itertuples(index=False):
        history = index.histories.get(str(query.customer_id), {})
        if {sku: value[2] for sku, value in history.items()} != dict(zip(query.history_skus, query.history_sku_invoice_counts)):
            raise ValueError("Validation query history differs from the existing paid policy")
        sources, scores = index.sources(query.customer_id)
        for rank, (sku, selected) in enumerate(interleave(sources, policy), 1):
            rows.append((cutoff, str(query.query_id), str(query.customer_id), sku, rank, selected) +
                tuple(sku in scores[source] for source in policy["source_order"]) +
                (scores["history"].get(sku, 0.0), scores["neighbors"].get(sku, 0.0),
                 scores["recent_popularity"].get(sku, 0), scores["long_popularity"].get(sku, 0)))
    return arrow_rows(rows, CANDIDATE_SCHEMA).to_pandas()


def read_inputs(cleaning_dir, snapshot_dir, retrieval_dir, config):
    snapshot = json.loads((snapshot_dir / "snapshot_report.json").read_text())
    cleaning = json.loads((cleaning_dir / "cleaning_report.json").read_text())
    retrieval = json.loads((retrieval_dir / "training_baseline_report.json").read_text())
    if sha256(DEFAULT_POLICY) != config["retrieval_policy_sha256"] or retrieval["policy_sha256"] != config["retrieval_policy_sha256"]:
        raise ValueError("Retrieval policy is not the frozen V1 expected by Milestone 2")
    paid_path = cleaning_dir / "paid_purchases.parquet"
    digest = sha256(paid_path)
    if digest != cleaning["outputs"][paid_path.name]["sha256"] or digest != snapshot["cleaning_file_sha256"][paid_path.name]:
        raise ValueError("Paid inputs do not match cleaning/snapshot provenance")
    for name in ["queries.parquet", "labels.parquet", "catalogs.parquet"]:
        if sha256(snapshot_dir / name) != snapshot["outputs"][name]["sha256"]:
            raise ValueError(f"Snapshot integrity mismatch: {name}")
    if sha256(retrieval_dir / "candidates.parquet") != retrieval["outputs"]["candidates.parquet"]["sha256"]:
        raise ValueError("Training candidates differ from the verified V1 run")
    # Only provenance and train/validation maturity are used from the legacy aggregate report.
    # No test partition rows or test outcomes enter this milestone.
    allowed_months = [month for month in snapshot["months"] if month["split"] in ["train", "validation"]]
    if any(not month["labels_mature"] for month in allowed_months):
        raise ValueError("All training/validation label windows must be mature")
    filters = [("split", "in", ["train", "validation"])]
    queries = pd.read_parquet(snapshot_dir / "queries.parquet", filters=filters)
    labels = pd.read_parquet(snapshot_dir / "labels.parquet", filters=filters)
    catalogs = pd.read_parquet(snapshot_dir / "catalogs.parquet", filters=filters)
    train_queries = queries.loc[queries.split.eq("train")]
    validation_queries = queries.loc[queries.split.eq("validation")]
    separation = verify_temporal_separation(train_queries, validation_queries, labels.loc[labels.split.eq("train")])
    for split in ["train", "validation"]:
        expected = {cutoff for cutoff, name in monthly_cutoffs() if name == split}
        if set(queries.loc[queries.split.eq(split)].cutoff) != expected:
            raise ValueError(f"Expected all fixed {split} cutoffs")
    bound = validation_queries.target_end.max()
    if bound != pd.Timestamp("2011-05-01"):
        raise ValueError("Validation must end before the unopened May 2011 test period")
    paid = pd.read_parquet(paid_path, columns=REQUIRED_COLUMNS + ["quantity"], filters=[("invoice_date", "<", bound)])
    if not paid.invoice_date.lt(bound).all() or not paid.is_paid_purchase.fillna(False).all() or not paid.record_id.is_unique:
        raise ValueError("Expected canonical paid pre-test transactions only")
    access = {"snapshot_splits_read": ["train", "validation"], "paid_upper_bound_exclusive": bound.isoformat(),
        "paid_rows_loaded": len(paid), "test_rows_loaded": 0,
        "legacy_report_access": "Integrity hashes, coverage metadata and train/validation maturity only; test outcome summaries are not used."}
    return paid, queries, labels, catalogs, separation, access, {"coverage_basis": snapshot["coverage_basis"], "observation_end": snapshot["observation_end"]}


def build_features(paid, queries, labels, catalogs, retrieval_dir, output_dir, schema, policy):
    statistics = {}
    validation_writer = pq.ParquetWriter(output_dir / "validation_candidates.parquet", CANDIDATE_SCHEMA, compression="zstd")
    try:
        for split in ["train", "validation"]:
            writer, months = None, []
            try:
                for cutoff in sorted(queries.loc[queries.split.eq(split)].cutoff.unique()):
                    started = time.perf_counter()
                    cutoff = pd.Timestamp(cutoff)
                    print(f"Building {split} features {cutoff.date()}", flush=True)
                    query = queries.loc[queries.cutoff.eq(cutoff)]
                    label = labels.loc[labels.cutoff.eq(cutoff)]
                    if split == "train":
                        candidates = pd.read_parquet(retrieval_dir / "candidates.parquet", filters=[("cutoff", "=", cutoff)])
                    else:
                        candidates = validation_candidates(paid, query, catalogs.loc[catalogs.cutoff.eq(cutoff)], policy)
                        validation_writer.write_table(pa.Table.from_pandas(candidates, schema=CANDIDATE_SCHEMA, preserve_index=False))
                    features = generate_features(paid, query, candidates, schema)
                    examples = make_examples(features, label)
                    examples["split"] = split
                    table = pa.Table.from_pandas(examples, preserve_index=False)
                    if writer is None:
                        writer = pq.ParquetWriter(output_dir / (split + "_features.parquet"), table.schema, compression="zstd")
                    writer.write_table(table)
                    counts = examples.groupby("query_id").size()
                    totals = examples.groupby("query_id").row_weight.sum()
                    if set(counts.index) != set(query.query_id) or not np.allclose(totals, 1):
                        raise ValueError("Every existing query must retain its complete candidate pool and equal total weight")
                    months.append({"cutoff": cutoff.isoformat(), "queries": len(query), "rows": len(examples),
                        "positive_rows": int(examples.label.sum()), "zero_positive_example_queries": int(examples.groupby("query_id").label.sum().eq(0).sum()),
                        "min_candidates": int(counts.min()), "max_candidates": int(counts.max()), "total_query_weight": float(totals.sum()),
                        "seconds": time.perf_counter() - started})
                    del features, examples, table, candidates
            finally:
                if writer is not None:
                    writer.close()
            statistics[split] = {"months": months, "rows": sum(month["rows"] for month in months),
                "queries": sum(month["queries"] for month in months), "positive_rows": sum(month["positive_rows"] for month in months),
                "seconds": sum(month["seconds"] for month in months), "sampling": "none"}
    finally:
        validation_writer.close()
    return statistics


def markdown_report(report):
    def value(number):
        return "null" if number is None else f"{number:.4f}"
    lines = ["# Milestone 2 temporal validation", "", "Training: March 2010–January 2011. Validation: February–April 2011. Test data was not loaded or evaluated.", "",
        "## Population and retrieval ceiling", "", "```json", json.dumps({"population": report["population"], "pools": report["pools"]}, indent=2), "```", "",
        "## Identical-pool ranking comparison", "",
        "| Ranker | NDCG@10 | Recall@10 | Repeat Recall@10 | Discovery Recall@10 | Mean monthly catalog coverage@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, metrics in report["rankers"].items():
        numbers = [metrics[metric]["mean"] for metric in ["ndcg_at_10", "recall_at_10", "repeat_recall_at_10", "discovery_recall_at_10"]]
        lines.append("| " + name + " | " + " | ".join(value(number) for number in numbers + [report["mean_monthly_coverage_at_10"][name]]) + " |")
    lines += ["", "## Monthly comparisons", "", "| Cutoff | Ranker | NDCG@10 | Recall@10 | Coverage@10 |", "| --- | --- | ---: | ---: | ---: |"]
    for month in report["months"]:
        for name, metrics in month["rankers"].items():
            lines.append(f"| {month['cutoff'][:10]} | {name} | {value(metrics['ndcg_at_10']['mean'])} | {value(metrics['recall_at_10']['mean'])} | {value(month['coverage_at_10'][name])} |")
    lines += ["", "## Past-only frequency and recency segments", "",
        "| Dimension | Segment | Queries / primary | Ranker | NDCG@10 | Recall@10 | Repeat Recall@10 | Discovery Recall@10 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for dimension, segments in report["segments"].items():
        for segment, data in segments.items():
            for name, metrics in data["rankers"].items():
                lines.append(f"| {dimension} | {segment} | {data['population']['queries']} / {data['population']['primary_queries']} | {name} | " +
                    " | ".join(value(metrics[key]["mean"]) for key in ["ndcg_at_10", "recall_at_10", "repeat_recall_at_10", "discovery_recall_at_10"]) + " |")
    lines += ["", "## Paired customer-cluster bootstrap versus strongest heuristic", "",
        "| Model | Baseline | NDCG difference | Descriptive 95% interval | Repeat Recall difference | Discovery Recall difference |",
        "| --- | --- | ---: | --- | ---: | ---: |"]
    for name, comparison in report["paired_customer_cluster_bootstrap"].items():
        interval = comparison["ndcg_difference"]
        lines.append(f"| {name} | {comparison['baseline']} | {value(interval['mean_difference'])} | [{value(interval['lower'])}, {value(interval['upper'])}] | {value(comparison['repeat_recall_difference'])} | {value(comparison['discovery_recall_difference'])} |")
    lines += ["", "Customers are resampled with replacement, keeping all their primary queries. Each replicate uses a pooled query mean. These post-selection validation intervals are descriptive; they are not proof of future business lift.", "",
        "## Advancement recommendation", "", "```json", json.dumps(report["selection"], indent=2), "```", "",
        "The predefined advancement screen requires at least +0.01 absolute NDCG, positive descriptive CI lower bound, and no more than 0.01 absolute Recall loss. This is a validation-only recommendation; no deployment or test evaluation occurred.", "",
        "## Runtime and memory", "", "```json", json.dumps(report["resources"], indent=2), "```", "",
        "Peak RSS is the cumulative Linux process high-water mark, not isolated per-model allocation. Scoring times include ordered feature extraction and preprocessing. All candidates and zero-purchase queries were retained; no negative sampling or class balancing.", "",
        "## Definitions and inherited assumptions", "", "```json", json.dumps(report["definitions"], indent=2), "```", "",
        "Customer-item history is lifetime past-only; customer aggregates and quantity use 180 days. Identified rows determine purchaser counts. Anonymous rows only supply catalog/last-seen evidence. Customer IDs and SKUs never enter the feature matrix. Logistic preprocessing fits training only; XGBoost uses native missing branches. Scores are raw margins, not calibrated probabilities.", "",
        "Return handling, unresolved-code exclusions (including M/S), eligibility and target definitions are unchanged. Label maturity inherits the existing coverage watermark assumption. Three XGBoost configurations were defined before validation evaluation; no random candidate-row splits, early stopping, additional configurations or class balancing were used.", "",
        "All rankers use identical frozen candidate pools. Missing relevant items set the retrieval ceiling; ranking can only improve ordering inside the pool. See error_analysis.md for inspected cases and run_manifest.json for hashes, versions, access filters, configurations and artifact verification.", ""]
    return "\n".join(lines)


def markdown_errors(analysis):
    lines = ["# Validation error analysis", "", f"Model: `{analysis['model']}`; strongest heuristic: `{analysis['heuristic']}`.", "", analysis["interpretation"], ""]
    for number, case in enumerate(analysis["cases"], 1):
        lines += [f"## {number}. {case['query_id']} — {case['selection_category']}", "",
            f"History: {case['historical_invoices']} invoices; recency {case['past_recency_days']:.2f} days. Known-catalog targets: {case['known_catalog_targets']}; unretrieved: {len(case['unretrieved_targets'])}.", "",
            f"NDCG@10: model {case['model_ndcg_at_10']:.4f}, heuristic {case['heuristic_ndcg_at_10']:.4f}, oracle {case['oracle_ndcg_at_10']:.4f}. Oracle Recall@10: {case['oracle_recall_at_10']:.4f}.", "",
            f"Top-10 repeat/discovery hits: model {case['model_repeat_discovery_hits']}; heuristic {case['heuristic_repeat_discovery_hits']}.", "",
            "Unretrieved known-catalog targets (retrieval failures): " + json.dumps(case["unretrieved_targets"]), "",
            "Retrieved positives absent from model top ten (compare the oracle to separate ranking loss from capacity): " + json.dumps(case["retrieved_positives_not_in_model_top10"]), "",
            "| Rank | Model SKU / target | Model score | Past invoices / days since | Heuristic SKU / target |", "| ---: | --- | ---: | --- | --- |"]
        for rank, (model, heuristic) in enumerate(zip(case["model_top10"], case["heuristic_top10"]), 1):
            age = "never" if model["past_purchase_age_days"] is None else f"{model['past_purchase_age_days']:.1f}"
            lines.append(f"| {rank} | {model['sku']} / {model['target']} | {model['ranking_score']:.4f} | {model['past_invoice_count']} / {age} | {heuristic['sku']} / {heuristic['target']} |")
        lines.append("")
    return "\n".join(lines)


def run(cleaning_dir, snapshot_dir, retrieval_dir, output_dir, schema_path=SCHEMA_PATH, model_config_path=CONFIG_PATH):
    started = time.perf_counter()
    cleaning_dir, snapshot_dir, retrieval_dir, output_dir = [Path(path).resolve() for path in [cleaning_dir, snapshot_dir, retrieval_dir, output_dir]]
    inputs = {"cleaning": cleaning_dir, "snapshots": snapshot_dir, "retrieval_v1": retrieval_dir, "raw": ROOT / "data/raw"}
    for source in inputs.values():
        if output_dir == source or source in output_dir.parents or output_dir in source.parents:
            raise ValueError("Milestone 2 outputs must be separate from every existing input directory")
    schema, config, policy = load_schema(schema_path), load_model_config(model_config_path), load_policy()
    protected = [DEFAULT_POLICY, ROOT / "config/stock_code_policy.json", ROOT / "scripts/clean_transactions.py",
        ROOT / "scripts/temporal_snapshots.py", ROOT / "scripts/retrieval.py", ROOT / "scripts/training_baselines.py", Path(schema_path), Path(model_config_path)]
    code_paths = [ROOT / "scripts" / name for name in ["ranking_features.py", "ranking_models.py", "ranking_evaluation.py", "milestone2.py"]]
    hashes_before = {name: cleaning_hashes(path) for name, path in inputs.items()}
    protected_hashes = {str(path): sha256(path) for path in protected + code_paths}
    paid, queries, labels, catalogs, separation, access, coverage = read_inputs(cleaning_dir, snapshot_dir, retrieval_dir, config)
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_json(output_dir / "feature_schema.json", schema)
    dump_json(output_dir / "model_configuration.json", config)
    dump_json(output_dir / "retrieval_policy_v1.json", policy)
    statistics = build_features(paid, queries, labels, catalogs, retrieval_dir, output_dir, schema, policy)
    del paid
    train = pd.read_parquet(output_dir / "train_features.parquet")
    validation = pd.read_parquet(output_dir / "validation_features.parquet")
    original = pd.read_parquet(output_dir / "validation_candidates.parquet", columns=["query_id", "sku", "history_score"])
    validation = validation.merge(original.rename(columns={"history_score": "original_history_score"}), on=["query_id", "sku"], validate="one_to_one", sort=False)
    del original
    resources, models = {}, ["logistic"] + [part["name"] for part in config["xgboost_configs"]]
    score_records = validation[["cutoff", "query_id", "customer_id", "sku"]].copy()
    for name in models:
        print(f"Training {name} on {len(train):,} rows; validation scoring {len(validation):,} rows", flush=True)
        artifact = fit_model(name, train, schema, config)
        path = output_dir / "models" / (name + ".joblib")
        save_artifact(artifact, path)
        score_start = time.perf_counter()
        scores = score_model(artifact, validation, config["score_batch_rows"])
        score_seconds = time.perf_counter() - score_start
        restored = load_artifact(path)
        reproduced = score_model(restored, validation, config["score_batch_rows"])
        if not np.array_equal(scores, reproduced):
            raise ValueError(f"Save/load changed scores: {name}")
        validation["score_" + name] = scores
        score_records["score_" + name] = scores
        resources[name] = {**artifact["training"], "validation_score_seconds": score_seconds,
            "validation_scoring_rows": len(validation), "score_batch_rows": config["score_batch_rows"], "save_load_exact_scores": True}
        print(f"Finished {name}: fit {artifact['training']['seconds']:.2f}s, score {score_seconds:.2f}s", flush=True)
        del artifact, restored, scores, reproduced
    del train
    score_records.to_parquet(output_dir / "validation_scores.parquet", index=False, compression="zstd")
    evaluation_start = time.perf_counter()
    report, details, recommendations = evaluate_validation(validation, queries.loc[queries.split.eq("validation")],
        labels.loc[labels.split.eq("validation")], catalogs.loc[catalogs.split.eq("validation")], models)
    select_and_compare(report, details, config)
    analysis = error_analysis(validation, queries.loc[queries.split.eq("validation")], labels.loc[labels.split.eq("validation")],
        details, report["selection"]["best_learned_ranker"], report["selection"]["strongest_heuristic"])
    details.to_parquet(output_dir / "validation_query_metrics.parquet", index=False, compression="zstd")
    recommendations.to_parquet(output_dir / "validation_recommendations.parquet", index=False, compression="zstd")
    report["resources"] = {"models": resources, "feature_build": statistics,
        "evaluation_bootstrap_error_analysis_seconds": time.perf_counter() - evaluation_start,
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "elapsed_seconds_so_far": time.perf_counter() - started}
    report["temporal_separation"], report["coverage_assumption"] = separation, coverage
    dump_json(output_dir / "validation_comparison_report.json", report)
    (output_dir / "validation_comparison_report.md").write_text(markdown_report(report), encoding="utf-8")
    dump_json(output_dir / "error_analysis.json", analysis)
    (output_dir / "error_analysis.md").write_text(markdown_errors(analysis), encoding="utf-8")
    dump_json(output_dir / "advancement_recommendation.json", report["selection"])
    artifacts = {}
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "run_manifest.json":
            continue
        info = {"sha256": sha256(path), "bytes": path.stat().st_size}
        if path.suffix == ".parquet":
            parquet = pq.ParquetFile(path)
            rows = sum(batch.num_rows for batch in parquet.iter_batches())
            if rows != parquet.metadata.num_rows:
                raise ValueError(f"Incomplete Parquet readback: {path.name}")
            info.update({"rows": rows, "complete_readback_verified": True})
        artifacts[str(path.relative_to(output_dir))] = info
    if hashes_before != {name: cleaning_hashes(path) for name, path in inputs.items()}:
        raise ValueError("An existing raw/cleaning/snapshot/retrieval input changed")
    if protected_hashes != {str(path): sha256(path) for path in protected + code_paths}:
        raise ValueError("Frozen policies, configurations or executing code changed during the run")
    manifest = {"milestone": 2, "status": "complete", "data_access": access, "temporal_separation": separation,
        "feature_schema": schema, "model_configuration": config, "retrieval_policy_sha256": sha256(DEFAULT_POLICY),
        "inputs_sha256": hashes_before, "protected_and_code_sha256": protected_hashes, "input_files_unchanged": True,
        "frozen_policies_unchanged": True, "model_save_load_exact_scores": True, "sampling": "none", "class_balancing": "none",
        "feature_dataset_statistics": statistics, "model_resources": resources, "artifacts": artifacts,
        "elapsed_seconds": time.perf_counter() - started, "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "versions": {"python": sys.version.split()[0], **{name: importlib.metadata.version(name) for name in
            ["pandas", "numpy", "pyarrow", "scipy", "scikit-learn", "xgboost-cpu", "joblib", "threadpoolctl"]}}}
    dump_json(output_dir / "run_manifest.json", manifest)
    print(f"Milestone 2 complete. Best learned ranker: {report['selection']['best_learned_ranker']}; advancement recommendation: {report['selection']['advance']}. Test remains unopened.", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaning-dir", type=Path, default=ROOT / "outputs/cleaning")
    parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "outputs/snapshots")
    parser.add_argument("--retrieval-dir", type=Path, default=ROOT / "outputs/retrieval_v1")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/milestone2")
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    parser.add_argument("--model-config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    try:
        run(args.cleaning_dir, args.snapshot_dir, args.retrieval_dir, args.output_dir, args.schema, args.model_config)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Milestone 2 failed: {error}\n")


if __name__ == "__main__":
    main()
