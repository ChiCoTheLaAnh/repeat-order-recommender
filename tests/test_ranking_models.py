"""Train-only preprocessing, persistence, deterministic ranking and cluster intervals."""

import importlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from scripts.ranking_features import load_schema


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path("scripts/ranking_models.py").exists(), "Weighted ranking models are required")
        self.module = importlib.import_module("scripts.ranking_models")
        self.schema = load_schema()
        self.config = json.loads(Path("config/ranking_models_v1.json").read_text())
        self.config["xgboost_configs"][0].update(n_estimators=4, max_depth=2, min_child_weight=0)

    def examples(self):
        frame = pd.DataFrame(0.0, index=range(8), columns=self.schema["features"])
        frame["ci_invoice_count"] = [0, 2] * 4
        frame["ci_previously_purchased"] = [0, 1] * 4
        frame["ci_days_since_last_purchase"] = [np.nan, 10] * 4
        frame["query_id"] = [f"query{i // 2}" for i in range(8)]
        frame["label"], frame["row_weight"] = [0, 1] * 4, .5
        return frame

    def test_preprocessing_uses_training_statistics_only(self):
        artifact = self.module.fit_model("logistic", self.examples(), self.schema, self.config)
        pipe = artifact["pipeline"]
        index = self.schema["features"].index("ci_days_since_last_purchase")
        self.assertAlmostEqual(pipe.named_steps["imputer"].statistics_[index], np.log1p(10), places=6)
        before = pipe.named_steps["scaler"].mean_.copy()
        validation = self.examples()
        validation["ci_days_since_last_purchase"] = 100000
        self.module.score_model(artifact, validation, batch_rows=3)
        np.testing.assert_array_equal(before, pipe.named_steps["scaler"].mean_)
        self.assertAlmostEqual(float(pipe.named_steps["scaler"].n_samples_seen_), 4)
        self.assertEqual(artifact["training"]["rows"], 8)
        self.assertEqual(artifact["training"]["total_weight"], 4)

    def test_both_models_save_load_exact_scores_and_column_order(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ["logistic", "xgb_shallow"]:
                with self.subTest(model=name):
                    frame = self.examples()
                    artifact = self.module.fit_model(name, frame, self.schema, self.config)
                    expected = self.module.score_model(artifact, frame)
                    path = Path(directory) / (name + ".joblib")
                    self.module.save_artifact(artifact, path)
                    restored = self.module.load_artifact(path)
                    actual = self.module.score_model(restored, frame[frame.columns[::-1]], batch_rows=3)
                    np.testing.assert_array_equal(expected, actual)
                    self.assertTrue(np.isfinite(actual).all())
                    self.assertGreater(actual[1], actual[0])

    def test_weighted_fit_rejects_unequal_query_weights(self):
        frame = self.examples()
        frame.loc[0, "row_weight"] = 2
        with self.assertRaisesRegex(ValueError, "weight"):
            self.module.fit_model("logistic", frame, self.schema, self.config)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path("scripts/ranking_evaluation.py").exists(), "Validation evaluation is required")
        self.module = importlib.import_module("scripts.ranking_evaluation")

    def test_deterministic_lexical_score_ties_and_no_duplicates(self):
        self.assertEqual(self.module.rank_scores(["B", "A", "C"], [1, 1, 0]), ["A", "B", "C"])
        with self.assertRaisesRegex(ValueError, "duplicate|unique"):
            self.module.rank_scores(["A", "A"], [1, 2])

    def test_heuristic_retains_original_float64_order_when_feature_scores_collide(self):
        frame = pd.DataFrame({"sku": ["B", "A"], "ci_previously_purchased": [1, 1],
            "history_score": np.array([1 + 1e-10, 1], dtype=np.float32),
            "original_history_score": [1 + 1e-10, 1], "recent_purchasers": [1, 1]})
        self.assertEqual(self.module.heuristic_order(frame, "personalized"), ["B", "A"])

    def test_selection_ties_and_predefined_advancement_screen(self):
        import copy
        config = json.loads(Path("config/ranking_models_v1.json").read_text())
        config["bootstrap"]["replicates"] = 100
        names = ["personalized", "recent_popularity", "interleaved", "logistic"] + [part["name"] for part in config["xgboost_configs"]]
        ndcgs = dict.fromkeys(names, .25)
        ndcgs.update(recent_popularity=.15, interleaved=.20)
        report = {"rankers": {name: {metric: {"mean": ndcgs[name] if metric == "ndcg_at_10" else .3}
            for metric in self.module.RANKING_METRICS} for name in names}}
        def details():
            return pd.DataFrame({"customer_id": ["A", "B"], **{name + "__ndcg_at_10": [ndcgs[name]] * 2 for name in names}})
        result = self.module.select_and_compare(copy.deepcopy(report), details(), config)
        self.assertEqual(result["selection"]["selected_xgboost"], "xgb_shallow")
        self.assertEqual(result["selection"]["strongest_heuristic"], "personalized")
        self.assertEqual(result["selection"]["advance"], "personalized")
        ndcgs["xgb_shallow"] = .259  # Positive CI, but less than the predefined 0.01 meaningful gain.
        report["rankers"]["xgb_shallow"]["ndcg_at_10"]["mean"] = .259
        result = self.module.select_and_compare(copy.deepcopy(report), details(), config)
        self.assertFalse(result["selection"]["meets_predefined_validation_threshold"])
        ndcgs["xgb_shallow"] = .261
        report["rankers"]["xgb_shallow"]["ndcg_at_10"]["mean"] = .261
        result = self.module.select_and_compare(copy.deepcopy(report), details(), config)
        self.assertEqual(result["selection"]["advance"], "xgb_shallow")
        report["rankers"]["xgb_shallow"]["recall_at_10"]["mean"] = .28  # More than 0.01 recall loss.
        result = self.module.select_and_compare(copy.deepcopy(report), details(), config)
        self.assertEqual(result["selection"]["advance"], "personalized")

    def test_error_analysis_fallback_has_ten_distinct_cases_when_categories_are_empty(self):
        t = pd.Timestamp("2011-02-01")
        ids = [f"Q{i:02}" for i in range(12)]
        queries = pd.DataFrame({"cutoff": [t] * 12, "split": ["validation"] * 12, "query_id": ids,
            "customer_id": ids, "historical_invoice_count": [10] * 12, "recency_days": [1] * 12})
        labels = pd.DataFrame({"cutoff": [t] * 12, "split": ["validation"] * 12,
            "query_id": ids, "sku": ["10001"] * 12, "label_type": ["repeat"] * 12})
        catalog = pd.DataFrame({"cutoff": [t], "split": ["validation"], "sku": ["10001"]})
        frame = pd.DataFrame({"query_id": ids, "sku": ["10001"] * 12, "candidate_rank": [1] * 12,
            "ci_previously_purchased": [1] * 12, "ci_invoice_count": [10] * 12,
            "ci_days_since_last_purchase": [1] * 12, "original_history_score": [1.] * 12,
            "recent_purchasers": [1] * 12, "score_logistic": [2.] * 12})
        _, details, _ = self.module.evaluate_validation(frame, queries, labels, catalog, ["logistic"])
        result = self.module.error_analysis(frame, queries, labels, details, "logistic", "personalized")
        self.assertEqual(len(result["cases"]), 10)
        self.assertEqual(len({case["query_id"] for case in result["cases"]}), 10)

    def test_paired_bootstrap_resamples_repeated_customer_clusters(self):
        frame = pd.DataFrame({"customer_id": ["A", "A", "B", "zero"],
            "model": [.4, .6, .1, np.nan], "heuristic": [.2, .2, .2, np.nan]})
        result = self.module.paired_bootstrap(frame, "model", "heuristic", replicates=1000, seed=42)
        self.assertEqual(result["customers"], 2)
        self.assertEqual(result["primary_queries"], 3)
        self.assertAlmostEqual(result["mean_difference"], (.2 + .4 - .1) / 3)
        self.assertAlmostEqual(result["lower"], -.1)
        self.assertAlmostEqual(result["upper"], .3)
        self.assertEqual(result, self.module.paired_bootstrap(frame, "model", "heuristic", replicates=1000, seed=42))

    def test_validation_keeps_zero_outside_only_queries_and_full_target_oracle(self):
        t = pd.Timestamp("2011-02-01")
        queries = pd.DataFrame({"cutoff": [t] * 3, "split": ["validation"] * 3,
            "query_id": ["A", "zero", "outside"], "customer_id": ["A", "zero", "outside"],
            "historical_invoice_count": [2, 5, 12], "recency_days": [30, 90, 180]})
        labels = pd.DataFrame({"cutoff": [t] * 3, "split": ["validation"] * 3,
            "query_id": ["A", "A", "outside"], "sku": ["10001", "10002", "future"],
            "label_type": ["repeat", "discovery", "outside_catalog"]})
        catalog = pd.DataFrame({"cutoff": [t] * 3, "split": ["validation"] * 3, "sku": ["10001", "10002", "10003"]})
        frame = pd.DataFrame({"query_id": ["A", "zero", "outside"], "sku": ["10001"] * 3,
            "candidate_rank": [1] * 3, "ci_previously_purchased": [1] * 3,
            "original_history_score": [1.] * 3, "recent_purchasers": [1] * 3, "score_logistic": [2.] * 3})
        report, details, recommendations = self.module.evaluate_validation(frame, queries, labels, catalog, ["logistic"])
        self.assertEqual(report["population"]["primary_queries"], 1)
        self.assertEqual(report["population"]["zero_purchase_queries"], 1)
        self.assertEqual(report["population"]["outside_only_queries"], 1)
        self.assertEqual(report["pools"]["candidate_recall"]["mean"], .5)
        self.assertAlmostEqual(report["pools"]["oracle_ndcg_at_10"]["mean"], 1 / (1 + 1 / np.log2(3)))
        self.assertEqual(report["rankers"]["logistic"]["recall_at_10"]["mean"], .5)
        self.assertEqual(report["rankers"]["logistic"]["discovery_recall_at_10"]["mean"], 0)
        self.assertEqual(report["rankers"]["logistic"]["discovery_recall_at_10"]["queries"], 1)
        self.assertAlmostEqual(report["mean_monthly_coverage_at_10"]["logistic"], 1 / 3)
        self.assertEqual(len(details), 3)
        self.assertEqual(len(recommendations.loc[recommendations.ranker.eq("logistic")]), 3)
        self.assertEqual(details.frequency_segment.tolist(), ["sparse_2_3", "moderate_4_9", "frequent_10_plus"])
        self.assertEqual(details.recency_segment.tolist(), ["recent_0_30", "active_30_90", "stale_90_180"])


if __name__ == "__main__":
    unittest.main()
