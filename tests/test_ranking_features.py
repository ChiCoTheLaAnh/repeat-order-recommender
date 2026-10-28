"""Feature values, event-time isolation, candidate labels and temporal guards."""

import importlib
import unittest

import numpy as np
import pandas as pd

from scripts.temporal_snapshots import build_snapshot
from tests.test_retrieval import paid


def candidates_for(query, skus):
    return pd.DataFrame([{"cutoff": query.cutoff, "query_id": query.query_id, "customer_id": query.customer_id,
        "sku": sku, "candidate_rank": i, "selected_source": "long_popularity", "source_history": False,
        "source_neighbors": False, "source_recent_popularity": False, "source_long_popularity": True,
        "history_score": 0.0, "neighbor_score": 0.0, "recent_purchasers": 0, "long_purchasers": 1}
        for i, sku in enumerate(skus, 1)])


class FeatureTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.assertTrue(Path("scripts/ranking_features.py").exists(), "Past-only feature module is required")
        self.module = importlib.import_module("scripts.ranking_features")
        self.schema = self.module.load_schema()

    def fixture(self):
        t = pd.Timestamp("2010-08-01")
        start = t - pd.DateOffset(days=180)
        events = [(pd.Timestamp(start.value - 1, unit="ns"), "A", "old", "10001", 7),
            (start, "A", "boundary", "10001", 2),
            (t - pd.DateOffset(days=10), "A", "new", "10001", 3),
            (t - pd.DateOffset(days=10), "A", "new", "10001", 4),
            (t - pd.DateOffset(days=30), "A", "recent", "10002", 1),
            (t - pd.DateOffset(days=30), "B", "b", "10001", 1),
            (pd.Timestamp((t - pd.DateOffset(days=30)).value - 1, unit="ns"), "C", "edge30", "10001", 1),
            (t - pd.DateOffset(days=1), None, "anon", "10001", 1),
            (t - pd.DateOffset(days=3), "B", "known", "10003", 1),
            (t, "A", "future", "10001", 99),
            (t, "A", "missing-from-pool", "10002", 1)]
        frame = paid(events)
        queries, labels, _, _ = build_snapshot(frame, t, "train", "2010-09-01")
        query = queries.loc[queries.customer_id.eq("A")].iloc[0]
        return frame, queries.loc[queries.customer_id.eq("A")], labels.loc[labels.customer_id.eq("A")], candidates_for(query, ["10001", "10003"])

    def test_hand_values_and_exact_window_boundaries(self):
        frame, queries, _, candidates = self.fixture()
        features = self.module.generate_features(frame, queries, candidates, self.schema)
        a, b = features.iloc[0], features.iloc[1]
        self.assertEqual(a.ci_previously_purchased, 1)
        self.assertEqual(a.ci_invoice_count, 3)  # Four lines, three distinct invoices.
        self.assertEqual(a.ci_quantity_180, 9)  # Excludes pre-window 7 and cutoff 99.
        self.assertEqual(a.ci_days_since_last_purchase, 10)
        self.assertEqual(a.item_purchasers_30, 2)
        self.assertEqual(a.item_purchasers_180, 3)
        self.assertEqual(a.item_days_since_last_purchase, 1)  # Anonymous catalog evidence.
        self.assertEqual(a.customer_invoice_count_180, 3)
        self.assertEqual(a.customer_sku_count_180, 2)
        self.assertEqual(a.customer_days_since_last_purchase_180, 10)
        self.assertEqual(b.ci_previously_purchased, 0)
        self.assertEqual(b.ci_invoice_count, 0)
        self.assertEqual(b.ci_quantity_180, 0)
        self.assertTrue(pd.isna(b.ci_days_since_last_purchase))
        self.assertEqual(b.item_days_since_last_purchase, 3)

    def test_future_additions_do_not_change_features_or_read_future_scope(self):
        frame, queries, _, candidates = self.fixture()
        past = frame.loc[frame.invoice_date.lt(queries.cutoff.iloc[0])]
        before = self.module.generate_features(past, queries, candidates, self.schema)
        # A future unresolved classification/identity must not enter any feature computation.
        future = paid([("2012-01-01", "A", "later", "M", 100)])
        after = self.module.generate_features(pd.concat([frame, future], ignore_index=True), queries, candidates, self.schema)
        pd.testing.assert_frame_equal(before, after)

    def test_no_unretrieved_positive_insertion_and_equal_query_weights(self):
        frame, queries, labels, candidates = self.fixture()
        features = self.module.generate_features(frame, queries, candidates, self.schema)
        zero = features.copy()
        zero["customer_id"], zero["query_id"] = "zero", "2010-08-01|zero"
        examples = self.module.make_examples(pd.concat([features, zero], ignore_index=True), labels)
        self.assertEqual(examples.label.tolist(), [1, 0, 0, 0])
        self.assertEqual(len(examples), 4)
        self.assertFalse(examples.sku.eq("10002").any())
        self.assertTrue(np.allclose(examples.groupby("query_id").row_weight.sum(), 1))
        self.assertEqual(examples.row_weight.tolist(), [.5, .5, .5, .5])

    def test_feature_ordering_does_not_include_numeric_ids_or_labels(self):
        frame, queries, labels, candidates = self.fixture()
        features = self.module.make_examples(self.module.generate_features(frame, queries, candidates, self.schema), labels)
        features["customer_id"] = "1234567"
        expected = self.module.feature_matrix(features, self.schema)
        actual = self.module.feature_matrix(features[features.columns[::-1]], self.schema)
        np.testing.assert_array_equal(expected, actual)
        self.assertEqual(actual.shape, (2, 19))
        self.assertEqual(actual.dtype, np.float32)
        with self.assertRaisesRegex(ValueError, "metadata|forbidden"):
            self.module.feature_matrix(features, {**self.schema, "features": ["customer_id"]})

    def test_training_label_windows_can_touch_but_not_overlap_validation(self):
        train = pd.DataFrame({"cutoff": [pd.Timestamp("2011-01-01")], "target_end": [pd.Timestamp("2011-02-01")], "split": ["train"]})
        validation = pd.DataFrame({"cutoff": [pd.Timestamp("2011-02-01")], "target_end": [pd.Timestamp("2011-03-01")], "split": ["validation"]})
        self.module.verify_temporal_separation(train, validation, train)
        overlap = train.copy()
        overlap["target_end"] = pd.Timestamp("2011-02-02")
        with self.assertRaisesRegex(ValueError, "overlap|ends after"):
            self.module.verify_temporal_separation(overlap, validation, overlap)


if __name__ == "__main__":
    unittest.main()
