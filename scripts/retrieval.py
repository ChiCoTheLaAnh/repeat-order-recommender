"""Past-only retrieval and binary-relevance metrics; no target labels enter retrieval."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from .audit_raw import ROOT
from .temporal_snapshots import recommendation_eligibility

DEFAULT_POLICY = ROOT / "config/retrieval_policy_v1.json"


def load_policy(path=DEFAULT_POLICY):
    policy = json.loads(Path(path).read_text())
    if policy["evaluated_split"] != "train" or not policy["frozen_before_training_evaluation"]:
        raise ValueError("Only the frozen training-only policy is supported")
    if set(policy["source_order"]) != set(policy["shares"]) or any(weight <= 0 for weight in policy["shares"].values()):
        raise ValueError("Source shares must be positive and match source names")
    return policy


def unique_order(values):
    return list(dict.fromkeys(values))


def ideal_dcg(positives, k=10):
    return sum(1 / math.log2(rank + 1) for rank in range(1, min(k, positives) + 1))


def recall(order, target):
    return len(set(order).intersection(target)) / len(target) if target else None


def ranking_metrics(order, target, repeat=(), discovery=(), k=10):
    order = unique_order(order)[:k]
    dcg = sum(1 / math.log2(rank + 1) for rank, sku in enumerate(order, 1) if sku in target)
    denominator = ideal_dcg(len(target), k)
    return {"ndcg_at_10": dcg / denominator if denominator else None,
            "recall_at_10": recall(order, target), "repeat_recall_at_10": recall(order, repeat),
            "discovery_recall_at_10": recall(order, discovery)}


def pool_metrics(order, target, repeat, discovery, pool_k, ranking_k=10):
    pool = unique_order(order)[:pool_k]
    positives = len(set(pool).intersection(target))
    denominator = ideal_dcg(len(target), ranking_k)
    return {"candidate_recall": recall(pool, target), "repeat_candidate_recall": recall(pool, repeat),
            "discovery_candidate_recall": recall(pool, discovery),
            "oracle_ndcg_at_10": ideal_dcg(positives, ranking_k) / denominator if denominator else None,
            "oracle_recall_at_10": min(ranking_k, positives) / len(target) if target else None}


def interleave(sources, policy, cap=None, omitted=None):
    cap = policy["candidate_cap"] if cap is None else cap
    active = [name for name in policy["source_order"] if name != omitted]
    positions = {name: 0 for name in active}
    emissions = {name: 0 for name in active}
    seen, result = set(), []
    while active and len(result) < cap:
        chosen = active[0]
        for name in active[1:]:
            if (emissions[name] + 1) * policy["shares"][chosen] < (emissions[chosen] + 1) * policy["shares"][name]:
                chosen = name
        values = sources.get(chosen, [])
        while positions[chosen] < len(values) and values[positions[chosen]] in seen:
            positions[chosen] += 1
        if positions[chosen] == len(values):
            active.remove(chosen)
            continue
        sku = values[positions[chosen]]
        positions[chosen] += 1
        emissions[chosen] += 1
        seen.add(sku)
        result.append((sku, chosen))
    return result


class RetrievalIndex:
    def __init__(self, paid, cutoff, catalog, policy):
        self.cutoff, self.policy, self.catalog = pd.Timestamp(cutoff), policy, set(catalog)
        # Filter event time before deriving eligibility or any item/customer statistics.
        past = paid.loc[paid.invoice_date.lt(self.cutoff)]
        flagged = recommendation_eligibility(past)
        history = past.loc[flagged.recommendation_eligible_v1]
        if set(history.sku.dropna()) != self.catalog:
            raise ValueError("Snapshot catalog does not match the eligible pre-cutoff catalog")
        identified = history.loc[history.customer_id.notna()]
        stats = identified.groupby(["customer_id", "sku"], sort=True).agg(
            invoice_count=("invoice_id", "nunique"), last_purchase_at=("invoice_date", "max"))
        self.histories = {}
        for customer, frame in stats.groupby(level=0, sort=True):
            values = {}
            for (_, sku), row in frame.iterrows():
                days = (self.cutoff.value - row.last_purchase_at.value) / 86400e9
                score = math.log1p(row.invoice_count) * math.exp(-days / policy["history_decay_days"])
                values[str(sku)] = (score, row.last_purchase_at, int(row.invoice_count))
            self.histories[str(customer)] = values
        recent = identified.loc[identified.invoice_date.ge(self.cutoff - pd.DateOffset(days=policy["recent_days"]))]
        longer = identified.loc[identified.invoice_date.ge(self.cutoff - pd.DateOffset(days=policy["long_days"]))]
        self.recent_popularity = {str(sku): int(count) for sku, count in recent.groupby("sku").customer_id.nunique().items()}
        self.long_popularity = {str(sku): int(count) for sku, count in longer.groupby("sku").customer_id.nunique().items()}
        self.popular_lists = {"recent_popularity": sorted(self.recent_popularity, key=lambda sku: (-self.recent_popularity[sku], sku)),
                              "long_popularity": sorted(self.long_popularity, key=lambda sku: (-self.long_popularity[sku], sku))}
        behavior = identified.loc[identified.invoice_date.ge(self.cutoff - pd.DateOffset(days=policy["behavior_days"]))]
        self.neighbors, self.diagnostics = self._neighbors(behavior)

    def _neighbors(self, behavior):
        pairs = behavior[["customer_id", "sku"]].drop_duplicates()
        items = sorted(pairs.sku.unique())
        customers = sorted(pairs.customer_id.unique())
        result = {str(sku): [] for sku in items}
        if pairs.empty:
            return result, {"identified_customers": 0, "behavior_items": 0, "binary_pairs": 0,
                            "neighbor_edges": 0, "intersection_dtype": "int64", "sparse_bytes": 0}
        row = pd.Categorical(pairs.customer_id, categories=customers).codes
        column = pd.Categorical(pairs.sku, categories=items).codes
        binary = sparse.csr_matrix((np.ones(len(pairs), dtype=np.int64), (row, column)), shape=(len(customers), len(items)), dtype=np.int64)
        intersections = (binary.T @ binary).tocsr()  # int64, not uint8: 300 common purchasers must remain 300.
        counts = np.asarray(binary.sum(axis=0)).ravel().astype(np.float64)
        intersections.setdiag(0)
        intersections.eliminate_zeros()
        for index, sku in enumerate(items):
            start, stop = intersections.indptr[index:index + 2]
            neighbors, common = intersections.indices[start:stop], intersections.data[start:stop]
            keep = common >= self.policy["minimum_shared_purchasers"]
            neighbors, common = neighbors[keep], common[keep]
            similarity = common.astype(np.float64) / np.sqrt(counts[index] * counts[neighbors])
            order = np.lexsort((neighbors, -similarity))[:self.policy["neighbors_per_item"]]
            result[str(sku)] = [(str(items[neighbors[position]]), float(similarity[position]), int(common[position])) for position in order]
        sparse_bytes = sum(array.nbytes for matrix in [binary, intersections] for array in [matrix.data, matrix.indices, matrix.indptr])
        return result, {"identified_customers": len(customers), "behavior_items": len(items), "binary_pairs": len(pairs),
                        "neighbor_edges": sum(map(len, result.values())), "intersection_dtype": str(intersections.dtype), "sparse_bytes": sparse_bytes}

    def sources(self, customer):
        history = self.histories.get(str(customer), {})
        history_scores = {sku: value[0] for sku, value in history.items()}
        seeds = sorted(history, key=lambda sku: (-history[sku][1].value, sku))[:self.policy["seed_items"]]
        denominator = sum(history_scores[sku] for sku in seeds)
        neighbor_scores = {}
        for seed in seeds:
            weight = history_scores[seed] / denominator if denominator > 0 else 1 / len(seeds)
            for sku, similarity, _ in self.neighbors.get(seed, []):
                neighbor_scores[sku] = neighbor_scores.get(sku, 0) + weight * similarity
        neighbor_scores = {sku: score for sku, score in neighbor_scores.items() if score > 0}
        scores = {"history": history_scores, "neighbors": neighbor_scores,
                  "recent_popularity": self.recent_popularity, "long_popularity": self.long_popularity}
        lists = {"history": sorted(history_scores, key=lambda sku: (-history_scores[sku], sku)),
                 "neighbors": sorted(neighbor_scores, key=lambda sku: (-neighbor_scores[sku], sku)), **self.popular_lists}
        return lists, scores

    def rank(self, pool, customer, baseline):
        history = self.histories.get(str(customer), {})
        if baseline == "recent_popularity":
            return sorted(pool, key=lambda sku: (-self.recent_popularity.get(sku, 0), sku))
        if baseline != "personalized":
            raise ValueError(f"Unknown baseline {baseline}")
        return sorted(pool, key=lambda sku: (0, -history[sku][0], sku) if sku in history else (1, -self.recent_popularity.get(sku, 0), sku))
