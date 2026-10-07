"""Evaluate the frozen retrieval policy on training snapshots only; no ML training."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import scipy

from .audit_raw import ROOT, sha256
from .retrieval import DEFAULT_POLICY, RetrievalIndex, interleave, load_policy, pool_metrics, ranking_metrics
from .temporal_snapshots import REQUIRED_COLUMNS, cleaning_hashes, distribution, monthly_cutoffs

BASELINES = ["recent_popularity", "personalized"]
COMMON = [("cutoff", pa.timestamp("ns")), ("query_id", pa.string()), ("customer_id", pa.string()), ("sku", pa.string())]
CANDIDATE_SCHEMA = pa.schema(COMMON + [("candidate_rank", pa.int64()), ("selected_source", pa.string())] +
    [("source_" + name, pa.bool_()) for name in ["history", "neighbors", "recent_popularity", "long_popularity"]] +
    [("history_score", pa.float64()), ("neighbor_score", pa.float64()),
     ("recent_purchasers", pa.int64()), ("long_purchasers", pa.int64())])
RECOMMENDATION_SCHEMA = pa.schema(COMMON + [("scope", pa.string()), ("baseline", pa.string()), ("rank", pa.int64())])
DIAGNOSTIC_SCHEMA = pa.schema(COMMON[:3] + [("primary_targets", pa.int64()), ("repeat_targets", pa.int64()),
    ("discovery_targets", pa.int64()), ("outside_catalog_targets", pa.int64()), ("candidate_count", pa.int64())])


class MacroMetrics:
    def __init__(self):
        self.values = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))

    def add(self, key, metrics):
        for name, value in metrics.items():
            cell = self.values[key][name]  # Register undefined metrics too.
            if value is not None:
                cell[0] += value
                cell[1] += 1

    def report(self):
        return {key: {name: {"mean": total / count if count else None, "queries": count, "sum": total}
                for name, (total, count) in metrics.items()} for key, metrics in sorted(self.values.items())}


def validate_training_months(months):
    if any(not month["labels_mature"] for month in months if month["split"] == "train"):
        raise ValueError("All training target months must be mature; partial labels cannot be evaluated")


def evaluate_month(paid, queries, labels, catalog, policy, overall=None):
    if queries.empty or not queries.split.eq("train").all() or not catalog.split.eq("train").all() or not labels.split.eq("train").all():
        raise ValueError("Expected nonempty train queries and train-only catalog/labels")
    cutoff = pd.Timestamp(queries.cutoff.iloc[0])
    if any(not frame.cutoff.eq(cutoff).all() for frame in [queries, labels, catalog]):
        raise ValueError("Each evaluation batch must have exactly one cutoff")
    started = time.perf_counter()
    index = RetrievalIndex(paid, cutoff, catalog.sku, policy)
    build_seconds = time.perf_counter() - started
    metrics = MacroMetrics()
    coverage = defaultdict(set)
    sizes = defaultdict(list)
    candidates, recommendations, diagnostics = [], [], []
    population = dict.fromkeys(["queries", "primary_queries", "zero_purchase_queries", "outside_only_queries",
                               "repeat_queries", "discovery_queries"], 0)
    targets = {str(query): {kind: set(part.sku) for kind, part in frame.groupby("label_type", sort=True)}
               for query, frame in labels.groupby("query_id", sort=True)}
    if not set(targets).issubset(set(queries.query_id)):
        raise ValueError("Labels must reference existing training queries")

    def add(key, values):
        metrics.add(key, values)
        if overall is not None:
            overall.add(key, values)

    for query in queries.sort_values("customer_id").itertuples(index=False):
        identity = (cutoff, str(query.query_id), str(query.customer_id))
        sources, scores = index.sources(query.customer_id)
        # Verify compatibility with the existing history snapshot, before touching target labels.
        history = index.histories.get(str(query.customer_id), {})
        expected = dict(zip(query.history_skus, query.history_sku_invoice_counts))
        if {sku: value[2] for sku, value in history.items()} != expected:
            raise ValueError("Paid history differs from its query snapshot")
        variants = {"full": interleave(sources, policy)}
        variants.update({"without_" + source: interleave(sources, policy, omitted=source) for source in policy["source_order"]})
        full = [sku for sku, _ in variants["full"]]
        for rank, (sku, selected) in enumerate(variants["full"], 1):
            candidates.append(identity + (sku, rank, selected) + tuple(sku in scores[source] for source in policy["source_order"]) +
                (scores["history"].get(sku, 0.0), scores["neighbors"].get(sku, 0.0),
                 scores["recent_popularity"].get(sku, 0), scores["long_popularity"].get(sku, 0)))
        standalone = {baseline: index.rank(index.catalog, query.customer_id, baseline)[:policy["ranking_k"]] for baseline in BASELINES}
        for scope, pool in [("catalog", standalone), ("candidate_200", {baseline: index.rank(full, query.customer_id, baseline)[:policy["ranking_k"]] for baseline in BASELINES})]:
            for baseline, order in pool.items():
                recommendations.extend(identity + (sku, scope, baseline, rank) for rank, sku in enumerate(order, 1))

        # Labels are accessed only after all candidate sources, merges, and standalone recommendations exist.
        label = targets.get(str(query.query_id), {})
        repeat, discovery, outside = [label.get(kind, set()) for kind in ["repeat", "discovery", "outside_catalog"]]
        target = repeat | discovery
        if not target.issubset(index.catalog) or outside.intersection(index.catalog):
            raise ValueError("Label categories disagree with the pre-cutoff catalog")
        population["queries"] += 1
        population["primary_queries"] += bool(target)
        population["zero_purchase_queries"] += not (target or outside)
        population["outside_only_queries"] += bool(outside) and not target
        population["repeat_queries"] += bool(repeat)
        population["discovery_queries"] += bool(discovery)
        diagnostics.append(identity + (len(target), len(repeat), len(discovery), len(outside), len(full)))
        for variant, merged in variants.items():
            order = [sku for sku, _ in merged]
            sizes[variant].append(len(order))
            for k in policy["candidate_ks"]:
                prefix = order[:k]
                key = f"{variant}/pool_{k}"
                coverage[key + "/candidates"].update(prefix)
                add(key + "/candidates", pool_metrics(prefix, target, repeat, discovery, k, policy["ranking_k"]))
                # All rankers get exactly this same pool. Interleaving itself is also a descriptive baseline.
                for baseline in ["interleaved"] + BASELINES:
                    ranked = prefix if baseline == "interleaved" else index.rank(prefix, query.customer_id, baseline)
                    top = ranked[:policy["ranking_k"]]
                    coverage[key + "/" + baseline].update(top)
                    add(key + "/" + baseline, ranking_metrics(top, target, repeat, discovery, policy["ranking_k"]))
        for baseline, top in standalone.items():
            key = "catalog/" + baseline
            coverage[key].update(top)
            add(key, ranking_metrics(top, target, repeat, discovery, policy["ranking_k"]))
    for name in ["primary_queries", "zero_purchase_queries", "outside_only_queries"]:
        population[name + "_fraction"] = population[name] / population["queries"]
    month = {"cutoff": cutoff.isoformat(), "split": "train", "catalog_size": len(index.catalog), "population": population,
        "metrics": metrics.report(), "catalog_coverage": {key: len(skus) / len(index.catalog) if index.catalog else None for key, skus in sorted(coverage.items())},
        "shortlist_sizes": {key: distribution(values) for key, values in sizes.items()},
        "index_diagnostics": index.diagnostics,
        "runtime_seconds": {"index_build": build_seconds, "retrieval_and_evaluation": time.perf_counter() - started - build_seconds},
        "process_peak_rss_mib_so_far": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return month, candidates, recommendations, diagnostics


def arrow_rows(rows, schema):
    return pa.Table.from_arrays([pa.array([row[i] for row in rows], type=field.type) for i, field in enumerate(schema)], schema=schema)


def markdown_report(report):
    def number(value):
        return "null" if value is None else f"{value:.4f}"
    lines = ["# Training-only retrieval and heuristic baselines", "", "Validation/test were not evaluated. No ML models were trained.", "",
        f"Policy: `{report['policy']['policy_version']}`; SHA-256: `{report['policy_sha256']}`.", "",
        "## Query population", "", "```json", json.dumps(report["population"], indent=2), "```", "",
        "## Full-policy pools (query macro; full known-catalog target denominators)", "",
        "| K | Candidate recall | Repeat recall | Discovery recall | Oracle NDCG@10 | Oracle Recall@10 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for k in report["policy"]["candidate_ks"]:
        metrics = report["metrics"][f"full/pool_{k}/candidates"]
        names = ["candidate_recall", "repeat_candidate_recall", "discovery_candidate_recall", "oracle_ndcg_at_10", "oracle_recall_at_10"]
        lines.append("| " + str(k) + " | " + " | ".join(number(metrics[name]["mean"]) for name in names) + " |")
    lines += ["", "## Ranked baselines and unrestricted known-catalog baselines", "",
        "| Scope | Ranker | NDCG@10 | Recall@10 | Repeat Recall@10 | Discovery Recall@10 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for scope in [f"full/pool_{k}" for k in report["policy"]["candidate_ks"]] + ["catalog"]:
        for baseline in (["interleaved"] if scope != "catalog" else []) + BASELINES:
            metrics = report["metrics"][scope + "/" + baseline]
            lines.append("| " + scope + " | " + baseline + " | " + " | ".join(number(metrics[name]["mean"]) for name in
                ["ndcg_at_10", "recall_at_10", "repeat_recall_at_10", "discovery_recall_at_10"]) + " |")
    lines += ["", "## Source ablations (same procedure, omitted source share redistributed)", "",
        "| Variant | Candidate Recall@200 | Personalized NDCG@10 | Personalized Recall@10 | Mean monthly candidate coverage |", "| --- | ---: | ---: | ---: | ---: |"]
    for variant in ["full"] + report["policy"]["ablations"]:
        key = variant + "/pool_200/"
        lines.append("| " + variant + " | " + " | ".join(number(value) for value in [
            report["metrics"][key + "candidates"]["candidate_recall"]["mean"],
            report["metrics"][key + "personalized"]["ndcg_at_10"]["mean"],
            report["metrics"][key + "personalized"]["recall_at_10"]["mean"],
            report["mean_monthly_catalog_coverage"][key + "candidates"]]) + " |")
    lines += ["", "## Monthly population, sizes, runtime and memory", "",
        "| Cutoff | Queries | Primary queries | Zero-purchase fraction | Outside-only queries | Full-list median / min | Index seconds | Evaluation seconds | Peak RSS MiB so far |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |"]
    for month in report["months"]:
        p, s, r = month["population"], month["shortlist_sizes"]["full"], month["runtime_seconds"]
        lines.append(f"| {month['cutoff'][:10]} | {p['queries']} | {p['primary_queries']} | {p['zero_purchase_queries_fraction']:.4f} | {p['outside_only_queries']} | {s['median']:.0f} / {s['min']:.0f} | {r['index_build']:.2f} | {r['retrieval_and_evaluation']:.2f} | {month['process_peak_rss_mib_so_far']:.1f} |")
    lines += ["", "## Definitions and implementation decisions", "", "```json", json.dumps(report["policy"], indent=2), "```", "",
        "All positive-target query metrics include empty lists as zero. Empty target segments are null and omitted from their segment macro means; each JSON metric includes its query denominator. Zero-purchase and outside-only queries remain in artifacts and coverage.", "",
        "Popularity and binary similarity use identified purchasers only; anonymous history contributes catalog evidence only. Similarity intersections are int64 and denominator products are float64. Time windows include their lower boundary and exclude cutoff time. Historical frequency counts distinct invoices; history scores use fractional elapsed days.", "",
        "No future positives enter retrieval. Catalog-only SKUs with zero popularity are included in standalone ranking, with lexical tie-breaking. Neighbor seeds use lifetime pre-cutoff history; the similarity matrix alone is limited to 180 days. Neighbor destinations may include already purchased items; source duplication is removed during interleaving.", "",
        "Coverage is per-cutoff returned SKU union / visible catalog size across all queries; overall coverage is the unweighted mean of monthly coverage, never a future catalog union. Overall target metrics pool query instances (a customer may appear in multiple months). Runtime includes all four ablations. Linux RSS is a cumulative process high-water mark, not isolated monthly allocation. Sparse byte counts exclude Python objects and input frames.", "",
        "The snapshot label maturity/observation coverage assumption is inherited. Stock-code classification, unresolved-code exclusion, customer eligibility, and return policy were not revised based on these results.", "",
        "The JSON contains every monthly metric, segment denominator, all prefix/ablation coverage, shortlist histograms, code/input hashes, output hashes, package versions and timings.", "",
        "## Integrity and resources", "", "```json", json.dumps({key: report[key] for key in
            ["input_files_unchanged", "raw_files_unchanged", "policy_unchanged", "runtime_seconds", "process_peak_rss_mib", "outputs", "versions"]}, indent=2), "```", ""]
    return "\n".join(lines)


def run(cleaning_dir, snapshot_dir, output_dir, policy_path=DEFAULT_POLICY):
    started = time.perf_counter()
    cleaning_dir, snapshot_dir, output_dir = [Path(path).resolve() for path in [cleaning_dir, snapshot_dir, output_dir]]
    raw_dir = ROOT / "data/raw"
    for source in [cleaning_dir, snapshot_dir, raw_dir]:
        if output_dir == source or source in output_dir.parents or output_dir in source.parents:
            raise ValueError("Retrieval outputs must be separate from cleaning, snapshots, and raw directories")
    policy = load_policy(policy_path)
    policy_hash = sha256(policy_path)
    before = {"cleaning": cleaning_hashes(cleaning_dir), "snapshots": cleaning_hashes(snapshot_dir)}
    raw_before = cleaning_hashes(raw_dir)
    snapshot_report = json.loads((snapshot_dir / "snapshot_report.json").read_text())
    cleaning_report = json.loads((cleaning_dir / "cleaning_report.json").read_text())
    paid_name = "paid_purchases.parquet"
    if before["cleaning"].get(paid_name) != cleaning_report["outputs"][paid_name]["sha256"] or before["cleaning"][paid_name] != snapshot_report["cleaning_file_sha256"][paid_name]:
        raise ValueError("Paid file must match both cleaning and snapshot reports")
    for name in ["queries.parquet", "labels.parquet", "catalogs.parquet", "recommendation_eligibility.parquet"]:
        if before["snapshots"].get(name) != snapshot_report["outputs"][name]["sha256"]:
            raise ValueError(f"Snapshot hash mismatch: {name}")
    months_info = [month for month in snapshot_report["months"] if month["split"] == "train"]
    validate_training_months(months_info)
    expected = {cutoff.isoformat() for cutoff, split in monthly_cutoffs() if split == "train"}
    if {month["cutoff"] for month in months_info} != expected:
        raise ValueError("Expected all eleven fixed training cutoffs")
    # Only train partitions are read for queries, labels and catalogs. Held-out outcomes are not evaluated.
    queries, labels, catalogs = [pd.read_parquet(snapshot_dir / name, filters=[("split", "=", "train")]) for name in
                                ["queries.parquet", "labels.parquet", "catalogs.parquet"]]
    if set(queries.cutoff.map(pd.Timestamp.isoformat)) != expected:
        raise ValueError("Query cutoffs differ from the fixed training schedule")
    paid = pd.read_parquet(cleaning_dir / paid_name, columns=REQUIRED_COLUMNS)
    if not paid.is_paid_purchase.fillna(False).all() or not paid.record_id.is_unique:
        raise ValueError("Expected canonical paid records")
    output_dir.mkdir(parents=True, exist_ok=True)
    overall, months = MacroMetrics(), []
    outputs = {name: {"rows": 0} for name in ["candidates.parquet", "baseline_recommendations.parquet", "query_diagnostics.parquet"]}
    schemas = [CANDIDATE_SCHEMA, RECOMMENDATION_SCHEMA, DIAGNOSTIC_SCHEMA]
    writers = [pq.ParquetWriter(output_dir / name, schema, compression="zstd") for name, schema in zip(outputs, schemas)]
    try:
        for cutoff in sorted(queries.cutoff.unique()):
            print(f"Evaluating train cutoff {pd.Timestamp(cutoff).date()}", flush=True)
            month, *rows = evaluate_month(paid, queries.loc[queries.cutoff.eq(cutoff)], labels.loc[labels.cutoff.eq(cutoff)],
                catalogs.loc[catalogs.cutoff.eq(cutoff)], policy, overall)
            write_start = time.perf_counter()
            for (name, stats), writer, schema, records in zip(outputs.items(), writers, schemas, rows):
                table = arrow_rows(records, schema)
                writer.write_table(table)
                stats["rows"] += len(records)
            month["runtime_seconds"]["artifact_write"] = time.perf_counter() - write_start
            months.append(month)
            del rows, table
    finally:
        for writer in writers:
            writer.close()
    for name, stats in outputs.items():
        path = output_dir / name
        restored = pq.ParquetFile(path)
        count = sum(batch.num_rows for batch in restored.iter_batches())  # Complete readback, bounded memory.
        if count != stats["rows"]:
            raise ValueError(f"Parquet readback row mismatch: {name}")
        stats.update({"sha256": sha256(path), "complete_readback_verified": True})
    if {"cleaning": cleaning_hashes(cleaning_dir), "snapshots": cleaning_hashes(snapshot_dir)} != before:
        raise ValueError("Cleaning or snapshot inputs changed during evaluation")
    if cleaning_hashes(raw_dir) != raw_before or sha256(policy_path) != policy_hash:
        raise ValueError("Raw files or frozen policy changed during evaluation")
    population = {key: sum(month["population"][key] for month in months) for key in months[0]["population"] if not key.endswith("_fraction")}
    for name in ["primary_queries", "zero_purchase_queries", "outside_only_queries"]:
        population[name + "_fraction"] = population[name] / population["queries"]
    report = {"evaluated_split": "train", "heldout_evaluated": False, "policy": policy, "policy_sha256": policy_hash,
        "population": population, "metrics": overall.report(), "months": months,
        "mean_monthly_catalog_coverage": {key: float(np.mean([month["catalog_coverage"][key] for month in months])) for key in months[0]["catalog_coverage"]},
        "input_sha256": before, "raw_sha256": raw_before, "input_files_unchanged": True, "raw_files_unchanged": True,
        "policy_unchanged": True, "outputs": outputs, "runtime_seconds": time.perf_counter() - started,
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "snapshot_coverage_basis": snapshot_report["coverage_basis"], "snapshot_observation_end": snapshot_report["observation_end"],
        "code_sha256": {name: sha256(ROOT / "scripts" / name) for name in ["retrieval.py", "training_baselines.py", "temporal_snapshots.py", "clean_transactions.py"]},
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "pyarrow": pa.__version__, "numpy": np.__version__, "scipy": scipy.__version__}}
    (output_dir / "training_baseline_report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (output_dir / "training_baseline_report.md").write_text(markdown_report(report))
    print(f"Wrote {outputs['candidates.parquet']['rows']:,} candidates for {population['queries']:,} training queries; held-out splits unevaluated", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaning-dir", type=Path, default=ROOT / "outputs/cleaning")
    parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "outputs/snapshots")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/retrieval_v1")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    try:
        run(args.cleaning_dir, args.snapshot_dir, args.output_dir, args.policy)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Training evaluation failed: {error}\n")


if __name__ == "__main__":
    main()
