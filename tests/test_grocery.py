import csv
import io
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.grocery import evaluate, load_trips, main, recommend


class GroceryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "purchases.csv"

    def load(self, rows):
        with self.path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["shopper_id", "trip_id", "purchase_date", "item"])
            for row in rows:
                writer.writerow(("s1", *row) if len(row) == 3 else row)
        return load_trips(self.path)

    def test_one_trip_returns_up_to_five_distinct_items_in_stable_order(self):
        trips = self.load([("t1", "2026-09-01", item) for item in
                           ("Rice", "Oats", "Milk", "Eggs", "Beans", "Apples")])
        cutoff = date(2026, 9, 4)
        expected = ["Apples", "Beans", "Eggs", "Milk", "Oats"]
        for method in ("cadence", "previous-trip"):
            result = recommend(trips, "s1", cutoff, method)
            self.assertEqual([item.item for item in result], expected)
            self.assertIn("3 days", result[0].reason)
        self.assertEqual(recommend(trips, "unknown", cutoff), [])

    def test_duplicate_rows_do_not_inflate_frequency_or_create_intervals(self):
        trips = self.load([
            ("t1", "2026-09-01", "Eggs"),
            ("t1", "2026-09-01", " eggs "),
            ("t1", "2026-09-01", "EGGS"),
            ("t1", "2026-09-01", "Greek   Yogurt"),
            ("t2", "2026-09-02", "Greek Yogurt"),
        ])
        result = recommend(trips, "s1", date(2026, 9, 4))
        self.assertEqual([item.item for item in result], ["Greek Yogurt", "Eggs"])
        self.assertIn("2 trips", result[0].reason)
        self.assertIn("1 trip", result[1].reason)

    def test_median_gap_due_priority_and_rule_specific_reasons(self):
        trips = self.load([
            ("t1", "2026-09-01", "eggs"), ("t1", "2026-09-01", "oats"),
            ("t2", "2026-09-07", "eggs"), ("t2", "2026-09-07", "oats"),
            ("t3", "2026-09-17", "eggs"),
            ("t4", "2026-09-25", "oats"), ("t4", "2026-09-25", "milk"),
        ])
        result = recommend(trips, "s1", date(2026, 9, 27))
        self.assertEqual([item.item for item in result], ["eggs", "milk"])
        self.assertIn("every 8 days", result[0].reason)
        self.assertIn("10 days", result[0].reason)
        self.assertIn("1 trip", result[1].reason)
        on_schedule = recommend(trips, "s1", date(2026, 10, 7))
        oats = next(item for item in on_schedule if item.item == "oats")
        self.assertIn("every 12 days", oats.reason)
        self.assertIn("12 days since", oats.reason)

    def test_cutoff_excludes_all_same_day_and_future_trips_and_other_shoppers(self):
        earlier = [("t1", "2026-09-01", "eggs"), ("t2", "2026-09-03", "oats")]
        other = [
            ("t3", "2026-09-09", "milk"), ("t4", "2026-09-09", "eggs"),
            ("t5", "2026-09-10", "beans"), ("s2", "t1", "2026-09-08", "rice"),
        ]
        cutoff = date(2026, 9, 9)
        history = self.load(earlier)
        for method in ("cadence", "previous-trip"):
            self.assertEqual(recommend(self.load(earlier + other), "s1", cutoff, method),
                             recommend(history, "s1", cutoff, method))

    def test_evaluator_uses_repeat_targets_and_excludes_held_out_rows(self):
        trips = self.load([
            ("t1", "2026-09-01", "eggs"), ("t1", "2026-09-01", "bread"),
            ("t2", "2026-09-03", "bread"),
            ("t3", "2026-09-09", "eggs"), ("t3", "2026-09-09", "milk"),
            ("t4", "2026-09-09", "bananas"),
            ("s2", "t1", "2026-09-03", "rice"),
        ])
        report = evaluate(trips)
        self.assertEqual(report["evaluated_trips"], 1)
        self.assertEqual(report["held_out_trips"], 2)
        self.assertEqual(report["no_repeat_target_trips"], 1)
        self.assertEqual(report["shoppers_without_prior_trips"], 1)
        self.assertEqual(report["metrics"]["previous-trip"],
                         {"precision_at_5": 0.0, "recall_at_5": 0.0})
        self.assertEqual(report["metrics"]["cadence"],
                         {"precision_at_5": 0.2, "recall_at_5": 1.0})

    def test_trip_ids_cannot_span_dates_for_same_shopper(self):
        with self.assertRaisesRegex(ValueError, "line 3.*multiple dates"):
            self.load([("t1", "2026-09-01", "eggs"), ("t1", "2026-09-03", "milk")])

    def test_synthetic_cli_verifies_protocol_without_reporting_metrics(self):
        self.load([("t1", "2026-09-01", "eggs"), ("t2", "2026-09-09", "eggs")])
        with redirect_stdout(io.StringIO()) as output:
            main(["evaluate", "--csv", str(self.path), "--data-kind", "synthetic"])
        self.assertIn("Synthetic demo", output.getvalue())
        self.assertNotIn("precision_at_5", output.getvalue())
