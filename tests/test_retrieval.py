"""Hand metrics, deterministic source merging, and event-time leakage checks."""

import importlib
import math
from pathlib import Path
import unittest

import pandas as pd

from scripts.audit_raw import EXPECTED_COLUMNS
from scripts.clean_transactions import normalize_transactions

ROOT = Path(__file__).resolve().parents[1]


def paid(events):
    rows = [[invoice, sku, "Product", quantity, pd.Timestamp(date), 2, customer, "UK"]
            for date, customer, invoice, sku, quantity in events]
    raw = pd.DataFrame(rows, columns=EXPECTED_COLUMNS)
    raw["source_sheet"], raw["source_row"] = "fixture", range(2, len(raw) + 2)
    frame = normalize_transactions(raw)
    return frame.loc[frame.is_paid_purchase].reset_index(drop=True)


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("scripts.retrieval")

    def test_hand_metrics_deduplicate_and_use_full_target_ideal(self):
        result = self.module.ranking_metrics(["A", "x", "x", "B"], {"A", "B", "C"}, {"A"}, {"B", "C"})
        ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(result["ndcg_at_10"], 1.5 / ideal)
        self.assertAlmostEqual(result["recall_at_10"], 2 / 3)
        self.assertEqual(result["repeat_recall_at_10"], 1)
        self.assertEqual(result["discovery_recall_at_10"], .5)

    def test_oracle_cannot_renormalize_away_missing_positives(self):
        result = self.module.pool_metrics(["A", "x"], {"A", "B", "C"}, {"A"}, {"B", "C"}, 50)
        ideal = 1 + 1 / math.log2(3) + .5
        self.assertAlmostEqual(result["candidate_recall"], 1 / 3)
        self.assertAlmostEqual(result["oracle_ndcg_at_10"], 1 / ideal)
        self.assertAlmostEqual(result["oracle_recall_at_10"], 1 / 3)
        self.assertEqual(result["discovery_candidate_recall"], 0)

    def test_empty_candidates_and_empty_segments_are_explicit(self):
        result = self.module.ranking_metrics([], {"A"}, set(), {"A"})
        self.assertEqual(result["ndcg_at_10"], 0)
        self.assertEqual(result["recall_at_10"], 0)
        self.assertIsNone(result["repeat_recall_at_10"])
        self.assertIsNone(self.module.ranking_metrics([], set())["ndcg_at_10"])
        self.assertIsNone(self.module.pool_metrics([], set(), set(), set(), 50)["candidate_recall"])

    def test_weighted_interleaving_dedup_backfill_and_prefix_consistency(self):
        sources = {"history": ["a", "b"], "neighbors": ["a", "c"],
                   "recent_popularity": ["b", "d"], "long_popularity": ["e"]}
        config = self.module.load_policy()
        merged = self.module.interleave(sources, config, cap=200)
        self.assertEqual([sku for sku, _ in merged], ["a", "c", "b", "d", "e"])
        self.assertEqual(self.module.interleave(sources, config, cap=3), merged[:3])
        self.assertEqual(self.module.interleave(sources, config), merged)
        disjoint = {name: [name + str(i) for i in range(300)] for name in config["source_order"]}
        full = self.module.interleave(disjoint, config)
        self.assertEqual({name: sum(source == name for _, source in full) for name in config["source_order"]},
                         {name: 2 * share for name, share in config["shares"].items()})
        ablated = self.module.interleave(disjoint, config, cap=100, omitted="history")
        self.assertEqual({name: sum(source == name for _, source in ablated) for name in config["source_order"]},
                         {"history": 0, "neighbors": 60, "recent_popularity": 30, "long_popularity": 10})


class RetrievalTests(unittest.TestCase):
    setUp = MetricTests.setUp
    def test_similarity_binary_counts_are_safe_above_uint8_and_no_self_neighbors(self):
        events = [("2010-02-01", str(i), str(i), sku, 1) for i in range(300) for sku in ["10001", "10002"]]
        # A second line for the same customer/SKU must not count a second shared purchaser.
        events += [("2010-02-01", "0", "0", "10001", 2)]
        index = self.module.RetrievalIndex(paid(events), pd.Timestamp("2010-03-01"), {"10001", "10002"}, self.module.load_policy())
        self.assertEqual(index.neighbors["10001"], [("10002", 1.0, 300)])
        self.assertEqual(index.neighbors["10002"], [("10001", 1.0, 300)])
        self.assertEqual(index.diagnostics["intersection_dtype"], "int64")

    def test_exact_30_and_180_day_boundaries_and_anonymous_catalog_only(self):
        t = pd.Timestamp("2010-08-01")
        events = [
            (t - pd.DateOffset(days=30), "A", "1", "10001", 1),
            (pd.Timestamp((t - pd.DateOffset(days=30)).value - 1, unit="ns"), "B", "2", "10002", 1),
            (t - pd.DateOffset(days=180), "C", "3", "10003", 1),
            (pd.Timestamp((t - pd.DateOffset(days=180)).value - 1, unit="ns"), "D", "4", "10004", 1),
            (t, "E", "5", "10005", 1),
            (t - pd.DateOffset(days=1), None, "6", "10006", 1),
        ]
        index = self.module.RetrievalIndex(paid(events), t, {"10001", "10002", "10003", "10004", "10006"}, self.module.load_policy())
        self.assertEqual(index.recent_popularity, {"10001": 1})
        self.assertEqual(index.long_popularity, {"10001": 1, "10002": 1, "10003": 1})
        self.assertNotIn("10006", index.neighbors)
        self.assertNotIn("10005", index.catalog)

    def test_history_frequency_seeds_ties_and_future_additions_are_invariant(self):
        past = [("2010-01-01", "A", "1", "10001", 1),
                ("2010-01-01", "A", "1", "10001", 2),
                ("2010-02-01", "A", "2", "10001", 1),
                ("2010-02-01", "A", "2", "10002", 1)]
        future = [("2010-03-01", "A", "3", "10003", 1),
                  ("2011-01-01", "B", "4", "10004", 1)]
        config = self.module.load_policy()
        before = self.module.RetrievalIndex(paid(past), pd.Timestamp("2010-03-01"), {"10001", "10002"}, config)
        after = self.module.RetrievalIndex(paid(past + future), pd.Timestamp("2010-03-01"), {"10001", "10002"}, config)
        sources, scores = before.sources("A")
        self.assertAlmostEqual(scores["history"]["10001"], math.log1p(2) * math.exp(-28 / 90))
        self.assertEqual(before.sources("A"), after.sources("A"))
        self.assertEqual(before.neighbors, after.neighbors)
        self.assertEqual(before.rank(["10002", "10001"], "A", "recent_popularity"), ["10001", "10002"])
        self.assertEqual(sources["history"], ["10001", "10002"])
        with self.assertRaisesRegex(ValueError, "catalog"):
            self.module.RetrievalIndex(paid(past), pd.Timestamp("2010-03-01"), {"10001", "10002", "future"}, config)

    def test_neighbors_need_five_purchasers_and_use_normalized_seed_weights(self):
        events = [("2010-02-01", str(i), str(i), sku, 1) for i in range(5) for sku in ["10001", "10002"]]
        events += [("2010-02-02", "0", "new", "10001", 1)]
        index = self.module.RetrievalIndex(paid(events), pd.Timestamp("2010-03-01"), {"10001", "10002"}, self.module.load_policy())
        _, scores = index.sources("0")
        first, second = scores["history"]["10001"], scores["history"]["10002"]
        self.assertAlmostEqual(scores["neighbors"]["10002"], first / (first + second))
        self.assertAlmostEqual(scores["neighbors"]["10001"], second / (first + second))
        fewer = self.module.RetrievalIndex(paid(events[:8]), pd.Timestamp("2010-03-01"), {"10001", "10002"}, self.module.load_policy())
        self.assertTrue(all(not value for value in fewer.neighbors.values()))

    def test_top_50_neighbors_lexical_ties_last_10_seeds_and_zero_weight_fallback(self):
        skus = [str(10000 + i) for i in range(55)]
        events = [("2010-02-01", str(i), str(i), sku, 1) for i in range(5) for sku in skus]
        index = self.module.RetrievalIndex(paid(events), pd.Timestamp("2010-03-01"), set(skus), self.module.load_policy())
        self.assertEqual([sku for sku, _, _ in index.neighbors[skus[0]]], skus[1:51])
        # Equal recency chooses lexically first ten seeds. Every seed excludes itself.
        _, scores = index.sources("0")
        self.assertAlmostEqual(scores["neighbors"][skus[0]], .9)
        self.assertAlmostEqual(scores["neighbors"][skus[10]], 1.0)
        self.assertNotIn(skus[-1], scores["neighbors"])
        # Make the last item uniquely recent: it displaces the tenth lexical seed.
        score, _, count = index.histories["0"][skus[-1]]
        index.histories["0"][skus[-1]] = (score, pd.Timestamp("2010-02-02"), count)
        _, scores = index.sources("0")
        self.assertAlmostEqual(scores["neighbors"][skus[9]], 1.0)
        # Underflow in extremely old histories must yield uniform seed weights, not NaN.
        index.histories["0"] = {sku: (0.0, date, count) for sku, (_, date, count) in index.histories["0"].items()}
        self.assertEqual(index.sources("0")[1]["neighbors"], scores["neighbors"])

    def test_personalized_ranker_purchased_first_and_popularity_fallback(self):
        events = [("2009-12-01", "A", "1", "10001", 1),
                  ("2010-02-20", "B", "2", "10002", 1),
                  ("2010-02-20", "C", "3", "10002", 1),
                  ("2010-02-20", "B", "4", "10003", 1),
                  ("2010-02-20", None, "5", "10004", 1)]
        index = self.module.RetrievalIndex(paid(events), pd.Timestamp("2010-03-01"), {"10001", "10002", "10003", "10004"}, self.module.load_policy())
        self.assertEqual(index.rank(index.catalog, "A", "personalized"), ["10001", "10002", "10003", "10004"])
        self.assertEqual(index.rank(index.catalog, "A", "recent_popularity"), ["10002", "10003", "10001", "10004"])


if __name__ == "__main__":
    unittest.main()
