"""Validation ranking metrics using unchanged V1 denominators and customer clusters."""

import numpy as np
import pandas as pd

from .retrieval import pool_metrics, ranking_metrics

HEURISTICS = ["personalized", "recent_popularity", "interleaved"]
RANKING_METRICS = ["ndcg_at_10", "recall_at_10", "repeat_recall_at_10", "discovery_recall_at_10"]
POOL_METRICS = ["candidate_recall", "repeat_candidate_recall", "discovery_candidate_recall", "oracle_ndcg_at_10", "oracle_recall_at_10"]


def rank_scores(skus, scores, k=None):
    if len(skus) != len(set(skus)):
        raise ValueError("Ranking requires unique SKUs, not duplicate recommendations")
    if len(skus) != len(scores) or not np.isfinite(scores).all():
        raise ValueError("Every unique SKU needs a finite score")
    ordered = [sku for sku, _ in sorted(zip(skus, scores), key=lambda pair: (-float(pair[1]), str(pair[0])))]
    return ordered if k is None else ordered[:k]


def paired_bootstrap(frame, model_column, baseline_column, replicates=2000, seed=42, confidence=.95):
    paired = frame[["customer_id", model_column, baseline_column]].dropna()
    if paired.empty:
        return {"primary_queries": 0, "customers": 0, "mean_difference": None, "lower": None, "upper": None}
    paired = paired.assign(difference=paired[model_column] - paired[baseline_column])
    clusters = paired.groupby("customer_id", sort=True).difference.agg(["sum", "count"])
    sums, counts = clusters["sum"].to_numpy(), clusters["count"].to_numpy()
    rng = np.random.default_rng(seed)
    differences = []
    for start in range(0, replicates, 100):
        indices = rng.integers(0, len(clusters), size=(min(100, replicates - start), len(clusters)))
        differences.extend((sums[indices].sum(axis=1) / counts[indices].sum(axis=1)).tolist())
    alpha = (1 - confidence) / 2
    lower, upper = np.quantile(differences, [alpha, 1 - alpha])
    return {"primary_queries": len(paired), "customers": len(clusters), "mean_difference": float(paired.difference.mean()),
        "lower": float(lower), "upper": float(upper), "confidence": confidence, "replicates": replicates, "seed": seed,
        "unit": "customer cluster", "weighting": "pooled query mean after clustered resampling",
        "interpretation": "Descriptive validation interval; model selection reused validation, and this is not proof of future business lift."}


def heuristic_order(frame, name):
    if name == "interleaved":
        return frame.sort_values("candidate_rank").sku.tolist()
    if name == "recent_popularity":
        return rank_scores(frame.sku.tolist(), frame.recent_purchasers.to_numpy())
    # Keep original float64 history scores; feature float32 rounding must not change V1 ties.
    rows = frame[["sku", "ci_previously_purchased", "original_history_score", "recent_purchasers"]].itertuples(index=False)
    return [row.sku for row in sorted(rows, key=lambda row: (0, -row.original_history_score, row.sku)
        if row.ci_previously_purchased else (1, -row.recent_purchasers, row.sku))]


def frequency_segment(count):
    return "sparse_2_3" if count <= 3 else "moderate_4_9" if count <= 9 else "frequent_10_plus"


def recency_segment(days):
    return "recent_0_30" if days <= 30 else "active_30_90" if days <= 90 else "stale_90_180"


def population(frame):
    n = len(frame)
    primary = int(frame.primary_targets.gt(0).sum())
    zero = int((frame.primary_targets + frame.outside_catalog_targets).eq(0).sum())
    outside = int((frame.primary_targets.eq(0) & frame.outside_catalog_targets.gt(0)).sum())
    return {"queries": n, "primary_queries": primary, "primary_fraction": primary / n if n else None,
        "zero_purchase_queries": zero, "zero_purchase_fraction": zero / n if n else None,
        "outside_only_queries": outside, "outside_only_fraction": outside / n if n else None,
        "repeat_queries": int(frame.repeat_targets.gt(0).sum()), "discovery_queries": int(frame.discovery_targets.gt(0).sum())}


def aggregate(frame, names, prefix=""):
    result = {}
    for name in names:
        values = frame[prefix + name].dropna()
        result[name] = {"mean": float(values.mean()) if len(values) else None, "queries": len(values)}
    return result


def summarize(frame, rankers):
    return {"population": population(frame), "pools": aggregate(frame, POOL_METRICS),
            "rankers": {name: aggregate(frame, RANKING_METRICS, name + "__") for name in rankers}}


def evaluate_validation(scored, queries, labels, catalogs, models):
    if queries.empty or any(not frame.split.eq("validation").all() for frame in [queries, labels, catalogs]):
        raise ValueError("Validation evaluator rejects training/test snapshots")
    if not set(scored.query_id).issubset(set(queries.query_id)) or scored.duplicated(["query_id", "sku"]).any():
        raise ValueError("Scores must cover unique candidates belonging to validation queries")
    if "cutoff" in scored and not scored.cutoff.isin(queries.cutoff).all():
        raise ValueError("Score cutoffs must belong to validation only")
    rankers = HEURISTICS + models
    targets = {str(query): {kind: set(part.sku) for kind, part in frame.groupby("label_type", sort=True)}
               for query, frame in labels.groupby("query_id", sort=True)}
    groups = scored.groupby("query_id", sort=False).indices
    catalog_sets = {pd.Timestamp(cutoff): set(frame.sku) for cutoff, frame in catalogs.groupby("cutoff", sort=True)}
    coverage = {(cutoff, ranker): set() for cutoff in catalog_sets for ranker in rankers}
    records, recommendations = [], []
    for query in queries.itertuples(index=False):
        frame = scored.iloc[groups.get(query.query_id, [])]
        cutoff = pd.Timestamp(query.cutoff)
        pool = frame.sort_values("candidate_rank").sku.tolist()
        kinds = targets.get(str(query.query_id), {})
        repeat, discovery, outside = [kinds.get(kind, set()) for kind in ["repeat", "discovery", "outside_catalog"]]
        target = repeat | discovery
        if not (set(pool) | target).issubset(catalog_sets[cutoff]) or outside.intersection(catalog_sets[cutoff]):
            raise ValueError("Candidate/label membership disagrees with the cutoff catalog")
        detail = {"cutoff": cutoff, "query_id": str(query.query_id), "customer_id": str(query.customer_id),
            "historical_invoice_count": int(query.historical_invoice_count), "recency_days": float(query.recency_days),
            "frequency_segment": frequency_segment(query.historical_invoice_count), "recency_segment": recency_segment(query.recency_days),
            "primary_targets": len(target), "repeat_targets": len(repeat), "discovery_targets": len(discovery),
            "outside_catalog_targets": len(outside), "candidate_count": len(pool), "retrieval_missed_targets": len(target.difference(pool))}
        detail.update(pool_metrics(pool, target, repeat, discovery, 200))
        for name in rankers:
            order = heuristic_order(frame, name) if name in HEURISTICS else rank_scores(frame.sku.tolist(), frame["score_" + name].to_numpy())
            top = order[:10]
            values = ranking_metrics(top, target, repeat, discovery)
            detail.update({name + "__" + key: value for key, value in values.items()})
            coverage[(cutoff, name)].update(top)
            recommendations.extend({"cutoff": cutoff, "query_id": query.query_id, "customer_id": query.customer_id,
                "ranker": name, "sku": sku, "rank": rank} for rank, sku in enumerate(top, 1))
        records.append(detail)
    details = pd.DataFrame(records)
    report = summarize(details, rankers)
    report["evaluated_split"], report["test_opened"], report["test_evaluated"] = "validation", False, False
    report["months"] = []
    for cutoff, frame in details.groupby("cutoff", sort=True):
        month = summarize(frame, rankers)
        month.update({"cutoff": cutoff.isoformat(), "catalog_size": len(catalog_sets[cutoff]),
            "coverage_at_10": {name: len(coverage[(cutoff, name)]) / len(catalog_sets[cutoff]) if catalog_sets[cutoff] else None for name in rankers}})
        report["months"].append(month)
    report["mean_monthly_coverage_at_10"] = {name: float(np.mean([month["coverage_at_10"][name] for month in report["months"]])) for name in rankers}
    report["segments"] = {column: {str(segment): summarize(frame, rankers) for segment, frame in details.groupby(column, sort=True)}
                          for column in ["frequency_segment", "recency_segment"]}
    report["definitions"] = {"metrics": "Unchanged scripts.retrieval functions; primary query macro over nonempty known-catalog targets. Repeat/discovery means use nonempty segment targets. Empty pools rank as zero on a positive target; zero-target metrics remain null.",
        "coverage": "Per cutoff: union of top-10 SKUs across all eligible queries / entire visible catalog size (including anonymous evidence). Overall: unweighted monthly mean. Never pool future catalogs.",
        "segments": "Lifetime distinct invoices: 2-3, 4-9, 10+. Last purchase age: [0,30], (30,90], (90,180] days. Segments use past-only snapshot features.",
        "comparability": "Every heuristic and model sees exactly the same frozen V1 candidate-200 pool; no unretrieved positives are inserted."}
    return report, details, pd.DataFrame(recommendations)


def select_and_compare(report, details, config):
    means = {name: value["ndcg_at_10"]["mean"] for name, value in report["rankers"].items()}
    if any(value is None for value in means.values()):
        raise ValueError("Model selection requires a nonempty primary validation population")
    strongest = max(HEURISTICS, key=lambda name: means[name])
    xgb_names = [part["name"] for part in config["xgboost_configs"]]
    selected_xgb = max(xgb_names, key=lambda name: means[name])
    models = ["logistic"] + xgb_names
    bootstrap = config["bootstrap"]
    comparisons = {}
    def difference(name, metric):
        model_value = report["rankers"][name][metric]["mean"]
        baseline_value = report["rankers"][strongest][metric]["mean"]
        return model_value - baseline_value if model_value is not None and baseline_value is not None else None
    for name in models:
        interval = paired_bootstrap(details, name + "__ndcg_at_10", strongest + "__ndcg_at_10",
            replicates=bootstrap["replicates"], seed=bootstrap["seed"], confidence=bootstrap["confidence"])
        comparisons[name] = {"baseline": strongest, "ndcg_difference": interval,
            "recall_difference": difference(name, "recall_at_10"),
            "repeat_recall_difference": difference(name, "repeat_recall_at_10"),
            "discovery_recall_difference": difference(name, "discovery_recall_at_10")}
    best = max(["logistic", selected_xgb], key=lambda name: means[name])
    comparison, thresholds = comparisons[best], config["advance"]
    interval = comparison["ndcg_difference"]
    advance = (interval["mean_difference"] >= thresholds["minimum_absolute_ndcg_gain"] and
        interval["lower"] > thresholds["minimum_ci_lower"] and comparison["recall_difference"] >= -thresholds["maximum_absolute_recall_loss"])
    report["selection"] = {"strongest_heuristic": strongest, "selected_xgboost": selected_xgb, "best_learned_ranker": best,
        "advance": best if advance else strongest, "meets_predefined_validation_threshold": advance,
        "scope": "Advancement recommendation only; no production deployment or test evaluation. Post-selection validation intervals are descriptive."}
    report["paired_customer_cluster_bootstrap"] = comparisons
    return report


def error_analysis(scored, queries, labels, details, model, heuristic, per_category=3):
    rows = details.loc[details.primary_targets.gt(0)].copy()
    rows["difference"] = rows[model + "__ndcg_at_10"] - rows[heuristic + "__ndcg_at_10"]
    groups = {"improvement": rows.loc[rows.difference.gt(0)].sort_values(["difference", "query_id"], ascending=[False, True]),
        "worse_ranking": rows.loc[rows.difference.lt(0)].sort_values(["difference", "query_id"]),
        "retrieval_miss": rows.loc[rows.retrieval_missed_targets.gt(0)].sort_values(["retrieval_missed_targets", "query_id"], ascending=[False, True]),
        "sparse_history": rows.loc[rows.historical_invoice_count.le(3)].sort_values(["historical_invoice_count", "query_id"])}
    chosen, tags = [], {}
    for category, group in groups.items():
        count = 0
        for row in group.itertuples(index=False):
            if row.query_id in tags:
                continue
            chosen.append(row.query_id)
            tags[row.query_id] = category
            count += 1
            if count == per_category:
                break
    if len(chosen) < 10:
        chosen.extend(query for query in rows.query_id if query not in chosen[:10])
        chosen = chosen[:max(10, len(tags))]
    cases = []
    query_lookup = queries.set_index("query_id")
    metric_lookup = details.set_index("query_id")
    for query_id in chosen:
        frame = scored.loc[scored.query_id.eq(query_id)]
        by_sku = frame.set_index("sku")
        query = query_lookup.loc[query_id]
        metric = metric_lookup.loc[query_id]
        label = labels.loc[labels.query_id.eq(query_id)].set_index("sku").label_type.to_dict()
        target = {sku for sku, kind in label.items() if kind != "outside_catalog"}
        model_top = rank_scores(frame.sku.tolist(), frame["score_" + model].to_numpy(), 10)
        heuristic_top = heuristic_order(frame, heuristic)[:10]
        def top_rows(order, is_model):
            result = []
            for sku in order:
                row = by_sku.loc[sku]
                result.append({"sku": sku, "target": label.get(sku, "none"),
                    "ranking_score": float(row["score_" + model]) if is_model else None,
                    "past_invoice_count": int(row.ci_invoice_count),
                    "past_purchase_age_days": float(row.ci_days_since_last_purchase) if pd.notna(row.ci_days_since_last_purchase) else None,
                    "candidate_position": int(row.candidate_rank)})
            return result
        model_hits = {kind: sum(label.get(sku) == kind for sku in model_top) for kind in ["repeat", "discovery"]}
        heuristic_hits = {kind: sum(label.get(sku) == kind for sku in heuristic_top) for kind in ["repeat", "discovery"]}
        cases.append({"query_id": query_id, "cutoff": query.cutoff.isoformat(), "customer_id": str(query.customer_id),
            "selection_category": tags.get(query_id, "additional_case"), "historical_invoices": int(query.historical_invoice_count),
            "past_recency_days": float(query.recency_days), "known_catalog_targets": len(target),
            "unretrieved_targets": [{"sku": sku, "type": label[sku]} for sku in sorted(target.difference(frame.sku))],
            "retrieved_positives_not_in_model_top10": sorted(target.intersection(frame.sku).difference(model_top)),
            "model_ndcg_at_10": float(metric[model + "__ndcg_at_10"]), "heuristic_ndcg_at_10": float(metric[heuristic + "__ndcg_at_10"]),
            "oracle_ndcg_at_10": float(metric.oracle_ndcg_at_10), "oracle_recall_at_10": float(metric.oracle_recall_at_10),
            "model_repeat_discovery_hits": model_hits, "heuristic_repeat_discovery_hits": heuristic_hits,
            "model_top10": top_rows(model_top, True), "heuristic_top10": top_rows(heuristic_top, False)})
    return {"model": model, "heuristic": heuristic, "cases": cases,
        "available_primary_queries_by_category": {name: len(group) for name, group in groups.items()},
        "interpretation": "Missing catalog targets are retrieval failures. The gap to oracle NDCG/Recall at ten is a ranking opportunity inside the fixed pool. Retrieved positives outside top ten also reflect the ten-item capacity, so not every omitted retrieved positive is recoverable. These selected cases are illustrative, not a random performance sample."}
