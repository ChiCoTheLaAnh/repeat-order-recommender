"""Boundary and leakage checks against the existing transaction cleaning policy."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from scripts.audit_raw import EXPECTED_COLUMNS, sha256
from scripts.clean_transactions import normalize_transactions


ROOT = Path(__file__).resolve().parents[1]


def paid_fixture(events):
    """Events: date, customer, invoice, SKU, optional quantity (distinct lines)."""
    rows = [[invoice, sku, "Product", event[4] if len(event) > 4 else 1,
             pd.Timestamp(date), 2.0, customer, "UK"]
            for event in events for date, customer, invoice, sku in [event[:4]]]
    raw = pd.DataFrame(rows, columns=EXPECTED_COLUMNS)
    raw["source_sheet"] = "fixture"
    raw["source_row"] = range(2, len(raw) + 2)
    normalized = normalize_transactions(raw)
    return normalized.loc[normalized.is_paid_purchase].reset_index(drop=True)


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.temporal = importlib.import_module("scripts.temporal_snapshots")

    def snapshot(self, events, cutoff="2010-03-01", observation_end="2010-04-01"):
        return self.temporal.build_snapshot(paid_fixture(events), pd.Timestamp(cutoff),
                                            "train", pd.Timestamp(observation_end))

    def test_schedule_uses_first_of_month_and_exact_requested_splits(self):
        schedule = self.temporal.monthly_cutoffs()
        self.assertEqual(len(schedule), 17)
        self.assertEqual(schedule[0], (pd.Timestamp("2010-03-01"), "train"))
        self.assertEqual(schedule[10], (pd.Timestamp("2011-01-01"), "train"))
        self.assertEqual(schedule[11], (pd.Timestamp("2011-02-01"), "validation"))
        self.assertEqual(schedule[13], (pd.Timestamp("2011-04-01"), "validation"))
        self.assertEqual(schedule[14], (pd.Timestamp("2011-05-01"), "test"))
        self.assertEqual(schedule[-1], (pd.Timestamp("2011-07-01"), "test"))

    def test_scope_eligibility_preserves_cleaning_and_excludes_unresolved_m_and_s(self):
        paid = paid_fixture([("2010-01-01", "A", "1", "10001"),
                             ("2010-01-02", "A", "2", "M"),
                             ("2010-01-03", "A", "3", "S")])
        before = paid.copy(deep=True)
        flagged = self.temporal.recommendation_eligibility(paid)
        self.assertEqual(flagged.recommendation_eligible_v1.tolist(), [True, False, False])
        self.assertEqual(len(flagged), len(paid))
        self.assertTrue(paid.is_paid_purchase.all())
        pd.testing.assert_frame_equal(paid, before)

    def test_exact_cutoffs_three_label_types_anonymous_catalog_and_zero_queries(self):
        q, labels, catalog, report = self.snapshot([
            ("2010-01-01", "A", "1", "10001"),
            ("2010-02-01", "A", "2", "10001"),
            ("2010-01-02", "B", "3", "10002"),
            ("2010-02-02", "B", "4", "10002"),
            ("2010-02-15", None, "5", "10003"),
            ("2010-03-01", "A", "6", "10001"),
            ("2010-03-15", "A", "7", "10003"),
            ("2010-03-20", "A", "8", "10004"),
            ("2010-03-31 23:59:59.999999999", "A", "12", "10004"),
            ("2010-03-21", None, "9", "10005"),
            ("2010-03-22", "A", "10", "M"),
            ("2010-04-01", "A", "11", "10006"),
        ])
        self.assertEqual(q.customer_id.tolist(), ["A", "B"])
        self.assertEqual(set(catalog.sku), {"10001", "10002", "10003"})
        self.assertEqual(dict(zip(labels.sku, labels.label_type)),
                         {"10001": "repeat", "10003": "discovery", "10004": "outside_catalog"})
        self.assertEqual(set(labels.customer_id), {"A"})
        self.assertEqual(report["fraction_queries_no_future_purchases"], 0.5)
        self.assertEqual(report["fraction_future_labels_outside_catalog"], 1 / 3)
        self.assertEqual(report["label_counts"], {"repeat": 1, "discovery": 1, "outside_catalog": 1})
        self.assertEqual(q.iloc[0].historical_invoice_count, 2)
        self.assertEqual(labels.loc[labels.sku.eq("10004"), "target_invoice_count"].iloc[0], 2)
        self.assertLess(q.iloc[0].last_purchase_at, pd.Timestamp("2010-03-01"))
        self.assertEqual(report["target_basket_size_distribution"]["histogram"], {"0": 1, "3": 1})

    def test_recent_activity_boundary_and_two_distinct_invoices_not_two_lines(self):
        cutoff = pd.Timestamp("2011-07-01")
        lower = cutoff - pd.DateOffset(days=180)
        q, _, _, _ = self.snapshot([
            ("2010-09-01", "included", "1", "10001"),
            (lower, "included", "2", "10001"),
            ("2010-09-01", "too_old", "3", "10001"),
            (pd.Timestamp(lower.value - 1, unit="ns"), "too_old", "4", "10001"),
            (lower, "one_invoice", "5", "10001", 1),
            (lower, "one_invoice", "5", "10002", 2),
            ("2011-06-01", "at_cutoff", "6", "10001"),
            (cutoff, "at_cutoff", "7", "10001"),
            ("2011-06-01", "before_cutoff", "8", "10001"),
            (pd.Timestamp(cutoff.value - 1, unit="ns"), "before_cutoff", "9", "10001"),
        ], cutoff=cutoff, observation_end="2011-08-01")
        self.assertEqual(set(q.customer_id), {"included", "before_cutoff"})

    def test_sku_frequency_and_targets_count_invoices_not_distinct_transaction_lines(self):
        q, labels, catalog, _ = self.snapshot([
            ("2010-01-01", "A", "1", "10001", 1),
            ("2010-01-01", "A", "1", "10001", 2),
            ("2010-02-01", "A", "2", "10001"),
            ("2010-03-01", "A", "3", "10001", 1),
            ("2010-03-01", "A", "3", "10001", 2),
            ("2010-03-02", "A", "4", "10001"),
        ])
        self.assertEqual(q.iloc[0].historical_invoice_count, 2)
        self.assertEqual(list(q.iloc[0].history_skus), ["10001"])
        self.assertEqual(list(q.iloc[0].history_sku_invoice_counts), [2])
        self.assertEqual(catalog.iloc[0].historical_invoice_count, 2)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels.iloc[0].target_invoice_count, 2)

    def test_immature_month_does_not_create_false_zero_labels_or_partial_baskets(self):
        events = [("2010-01-01", "A", "1", "10001"),
                  ("2010-02-01", "A", "2", "10001"),
                  ("2010-03-10", "A", "3", "10002")]
        q, labels, catalog, report = self.snapshot(events, observation_end="2010-03-31 23:59:59.999999999")
        self.assertEqual(len(q), 1)
        self.assertTrue(labels.empty)
        self.assertFalse(report["labels_mature"])
        self.assertIsNone(report["fraction_queries_no_future_purchases"])
        self.assertIsNone(report["target_basket_size_distribution"])
        mature_q, mature_labels, mature_catalog, mature_report = self.snapshot(events)
        pd.testing.assert_frame_equal(q, mature_q)
        pd.testing.assert_frame_equal(catalog, mature_catalog)
        self.assertEqual(len(mature_labels), 1)
        self.assertTrue(mature_report["labels_mature"])

    def test_future_data_can_change_labels_but_not_earlier_queries_or_catalog(self):
        past = [("2010-01-01", "A", "1", "10001"),
                ("2010-02-01", "A", "2", "10001"),
                ("2010-02-15", None, "3", "10002")]
        before_q, before_labels, before_catalog, _ = self.snapshot(past)
        future = [("2010-03-01", "A", "4", "10003"),
                  ("2010-03-02", "new_customer", "5", "10004"),
                  ("2010-03-03", None, "6", "10005"),
                  ("2012-01-01", "A", "7", "10006")]
        after_q, after_labels, after_catalog, _ = self.snapshot(past + future)
        pd.testing.assert_frame_equal(before_q, after_q)
        pd.testing.assert_frame_equal(before_catalog, after_catalog)
        self.assertTrue(before_labels.empty)
        self.assertEqual(after_labels.sku.tolist(), ["10003"])

    def test_no_history_has_typed_empty_outputs_and_defined_zero_denominators(self):
        q, labels, catalog, report = self.snapshot([("2010-03-01", "A", "1", "10001")])
        self.assertTrue(q.empty and labels.empty and catalog.empty)
        self.assertEqual(report["queries"], 0)
        self.assertIsNone(report["fraction_queries_no_future_purchases"])
        self.assertIsNone(report["fraction_future_labels_outside_catalog"])

    def test_cli_writes_all_months_and_preserves_every_cleaning_file(self):
        with tempfile.TemporaryDirectory() as folder:
            cleaning = Path(folder) / "cleaning"
            output = Path(folder) / "snapshots"
            cleaning.mkdir()
            paid = paid_fixture([("2010-01-01", "A", "1", "10001"),
                                 ("2010-02-01", "A", "2", "10001"),
                                 ("2010-03-02", "A", "3", "10002"),
                                 ("2010-03-03", None, "4", "10003"),
                                 ("2010-03-04", "A", "5", "M")])
            path = cleaning / "paid_purchases.parquet"
            paid.to_parquet(path, index=False)
            report = {"outputs": {path.name: {"sha256": sha256(path)}}, "policy_version": 1,
                      "stock_policy_sha256": "fixture_policy"}
            (cleaning / "cleaning_report.json").write_text(json.dumps(report))
            (cleaning / "keep.txt").write_text("Preserve this cleaning output too")
            before = {p.name: sha256(p) for p in cleaning.iterdir()}
            command = [sys.executable, "-m", "scripts.temporal_snapshots", "--cleaning-dir", str(cleaning),
                       "--output-dir", str(output), "--observation-end", "2011-08-01"]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual({p.name: sha256(p) for p in cleaning.iterdir()}, before)
            queries = pd.read_parquet(output / "queries.parquet")
            labels = pd.read_parquet(output / "labels.parquet")
            catalogs = pd.read_parquet(output / "catalogs.parquet")
            summary = json.loads((output / "snapshot_report.json").read_text())
            self.assertEqual(len(summary["months"]), 17)
            self.assertTrue(summary["cleaning_outputs_unchanged"])
            self.assertEqual(len(queries), sum(month["queries"] for month in summary["months"]))
            self.assertEqual(set(queries.customer_id), {"A"})
            self.assertFalse(labels.sku.eq("M").any() or catalogs.sku.eq("M").any())
            flags = pd.read_parquet(output / "recommendation_eligibility.parquet")
            self.assertEqual(len(flags), len(paid))
            self.assertEqual(int(flags.recommendation_eligible_v1.sum()), len(paid) - 1)
            self.assertTrue((output / "snapshot_report.md").exists())


if __name__ == "__main__":
    unittest.main()
