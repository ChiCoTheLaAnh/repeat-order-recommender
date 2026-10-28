"""Small end-to-end evaluation checks, independent of the downloaded workbook."""

import importlib
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from scripts.temporal_snapshots import build_snapshot
from scripts.audit_raw import sha256
from tests.test_retrieval import paid


class TrainingEvaluationTests(unittest.TestCase):
    def test_labels_cannot_change_candidates_and_zero_target_queries_remain(self):
        module = importlib.import_module("scripts.training_baselines")
        from scripts.retrieval import load_policy
        events = [("2010-01-01", "A", "1", "10001", 1),
                  ("2010-02-01", "A", "2", "10002", 1),
                  ("2010-01-01", "B", "3", "10001", 1),
                  ("2010-02-01", "B", "4", "10002", 1),
                  ("2010-02-02", None, "anonymous", "10004", 1),
                  ("2010-03-01", "A", "5", "10001", 1),
                  ("2010-03-02", "A", "6", "10003", 1)]
        frame = paid(events)
        q, labels, cat, _ = build_snapshot(frame, "2010-03-01", "train", "2010-04-01")
        month, candidates, recommendations, diagnostics = module.evaluate_month(frame, q, labels, cat, load_policy())
        changed = labels.copy()
        changed.loc[changed.label_type.eq("repeat"), "sku"] = "10002"
        other = module.evaluate_month(frame, q, changed, cat, load_policy())
        self.assertEqual(candidates, other[1])
        self.assertEqual(recommendations, other[2])
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(month["population"]["primary_queries"], 1)
        self.assertEqual(month["population"]["zero_purchase_queries"], 1)
        result = month["metrics"]["full/pool_200/candidates"]
        self.assertEqual(result["candidate_recall"]["mean"], 1)
        self.assertEqual(result["candidate_recall"]["queries"], 1)
        self.assertTrue(any(row[4] == "catalog" for row in recommendations))
        self.assertTrue(any(row[3] == "10004" and row[4] == "catalog" for row in recommendations))
        self.assertFalse(any(row[3] in {"10003", "10004"} for row in candidates))

    def test_maturity_and_split_are_guarded(self):
        module = importlib.import_module("scripts.training_baselines")
        from scripts.retrieval import load_policy
        frame = paid([("2010-01-01", "A", "1", "10001", 1),
                      ("2010-02-01", "A", "2", "10001", 1)])
        q, labels, cat, _ = build_snapshot(frame, "2010-03-01", "validation", "2010-04-01")
        with self.assertRaisesRegex(ValueError, "train"):
            module.evaluate_month(frame, q, labels, cat, load_policy())
        with self.assertRaisesRegex(ValueError, "mature"):
            module.validate_training_months([{"split": "train", "labels_mature": False}])

    def test_runner_preserves_inputs_and_reads_only_training_snapshot_partitions(self):
        module = importlib.import_module("scripts.training_baselines")
        from scripts.temporal_snapshots import cleaning_hashes, run as snapshot_run
        events = [(date, "A", str(i), "10001", 1) for i, date in enumerate(pd.date_range("2010-01-01", "2011-01-01", freq="MS"))]
        events += [("2010-01-02", "A", "extra", "10001", 1),
                   ("2010-02-01", None, "anonymous", "10002", 1),
                   ("2011-03-01", "A", "heldout", "90000", 1)]
        with tempfile.TemporaryDirectory() as folder:
            cleaning, snapshots, output = [Path(folder) / name for name in ["cleaning", "snapshots", "retrieval"]]
            cleaning.mkdir()
            path = cleaning / "paid_purchases.parquet"
            paid(events).to_parquet(path, index=False)
            source = {"outputs": {path.name: {"sha256": sha256(path)}}, "policy_version": 1,
                      "stock_policy_sha256": "fixture"}
            (cleaning / "cleaning_report.json").write_text(json.dumps(source))
            with contextlib.redirect_stdout(io.StringIO()):
                snapshot_run(cleaning, snapshots, "2011-08-01")
            before = [cleaning_hashes(directory) for directory in [cleaning, snapshots]]
            with mock.patch.object(module.pd, "read_parquet", wraps=pd.read_parquet) as reader, contextlib.redirect_stdout(io.StringIO()):
                report = module.run(cleaning, snapshots, output)
            snapshot_reads = [call for call in reader.call_args_list if Path(call.args[0]).parent == snapshots]
            self.assertEqual(len(snapshot_reads), 3)
            self.assertTrue(all(call.kwargs["filters"] == [("split", "=", "train")] for call in snapshot_reads))
            self.assertEqual(before, [cleaning_hashes(directory) for directory in [cleaning, snapshots]])
            self.assertEqual(len(report["months"]), 11)
            self.assertEqual(report["population"]["queries"], 11)
            self.assertFalse(report["heldout_evaluated"])
            self.assertFalse(pd.read_parquet(output / "candidates.parquet").sku.eq("90000").any())
            self.assertTrue(all(stats["complete_readback_verified"] for stats in report["outputs"].values()))


if __name__ == "__main__":
    unittest.main()
