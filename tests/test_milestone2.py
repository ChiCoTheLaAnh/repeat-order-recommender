"""One-command orchestration on small fixtures, with an unopened test partition."""

import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from scripts.audit_raw import sha256
from scripts.temporal_snapshots import cleaning_hashes, run as snapshot_run
from scripts.training_baselines import run as retrieval_run
from tests.test_retrieval import paid


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path("scripts/milestone2.py").exists(), "One-command Milestone 2 runner is required")
        self.module = importlib.import_module("scripts.milestone2")

    def test_full_run_preserves_inputs_and_excludes_test_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cleaning, snapshots, retrieval, output = [root / name for name in ["cleaning", "snapshots", "retrieval", "milestone2"]]
            cleaning.mkdir()
            events = []
            for i, date in enumerate(pd.date_range("2010-01-01", "2011-07-01", freq="MS")):
                events += [(date, "A", f"{i}a", "10001", 1), (date, "B", f"{i}b", "10002", 1)]
            events += [("2010-01-02", "A", "extra_a", "10002", 1),
                       ("2010-01-02", "B", "extra_b", "10001", 1),
                       ("2011-05-01", "A", "test_new", "90000", 1)]
            path = cleaning / "paid_purchases.parquet"
            paid(events).to_parquet(path, index=False)
            source = {"outputs": {path.name: {"sha256": sha256(path)}}, "policy_version": 1, "stock_policy_sha256": "fixture"}
            (cleaning / "cleaning_report.json").write_text(json.dumps(source))
            config = json.loads(Path("config/ranking_models_v1.json").read_text())
            config["xgboost_configs"] = [config["xgboost_configs"][0]]
            config["xgboost_configs"][0].update(n_estimators=4, max_depth=2, min_child_weight=0)
            config["bootstrap"]["replicates"] = 100
            config_path = root / "model_config.json"
            config_path.write_text(json.dumps(config))
            with contextlib.redirect_stdout(io.StringIO()):
                snapshot_run(cleaning, snapshots, "2011-08-01")
                retrieval_run(cleaning, snapshots, retrieval)
            before = {name: cleaning_hashes(path) for name, path in [("cleaning", cleaning), ("snapshots", snapshots), ("retrieval", retrieval)]}
            with mock.patch.object(self.module.pd, "read_parquet", wraps=pd.read_parquet) as reader, contextlib.redirect_stdout(io.StringIO()):
                report = self.module.run(cleaning, snapshots, retrieval, output, model_config_path=config_path)
            snapshot_reads = [call for call in reader.call_args_list if Path(call.args[0]).parent == snapshots]
            self.assertEqual(len(snapshot_reads), 3)
            self.assertTrue(all(call.kwargs["filters"] == [("split", "in", ["train", "validation"])] for call in snapshot_reads))
            paid_reads = [call for call in reader.call_args_list if Path(call.args[0]) == path]
            self.assertEqual(len(paid_reads), 1)
            self.assertEqual(paid_reads[0].kwargs["filters"], [("invoice_date", "<", pd.Timestamp("2011-05-01"))])
            self.assertEqual(before, {name: cleaning_hashes(path) for name, path in [("cleaning", cleaning), ("snapshots", snapshots), ("retrieval", retrieval)]})
            manifest = json.loads((output / "run_manifest.json").read_text())
            self.assertEqual(manifest["data_access"]["snapshot_splits_read"], ["train", "validation"])
            self.assertEqual(manifest["data_access"]["paid_upper_bound_exclusive"], "2011-05-01T00:00:00")
            self.assertFalse(report["test_evaluated"])
            self.assertFalse(report["test_opened"])
            train = pd.read_parquet(output / "train_features.parquet")
            validation = pd.read_parquet(output / "validation_features.parquet")
            self.assertEqual(set(train.split), {"train"})
            self.assertEqual(set(validation.split), {"validation"})
            self.assertFalse(train.sku.eq("90000").any() or validation.sku.eq("90000").any())
            self.assertTrue((output / "models/logistic.joblib").exists())
            self.assertTrue((output / "models/xgb_shallow.ubj").exists())
            self.assertTrue(manifest["model_save_load_exact_scores"])
            self.assertEqual(len(report["months"]), 3)


if __name__ == "__main__":
    unittest.main()
