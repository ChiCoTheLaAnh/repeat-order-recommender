"""Policy tests using native business rows, including intentionally overlapping flags."""

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from scripts.audit_raw import EXPECTED_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


def rows(values):
    frame = pd.DataFrame(values, columns=EXPECTED_COLUMNS)
    frame["source_sheet"] = ["first"] * len(frame)
    frame["source_row"] = range(2, len(frame) + 2)
    return frame


def sale(invoice="100", sku="85123A", customer=123.0, quantity=2, price=3,
         description="WHITE HANGING HEART T-LIGHT HOLDER", date="2010-12-01"):
    return [invoice, sku, description, quantity, pd.Timestamp(date), price, customer, "UK"]


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.policy = importlib.import_module("scripts.clean_transactions")

    def test_missing_ids_are_nullable_and_numeric_ids_have_no_decimal_suffix(self):
        source = rows([sale(invoice=100.0, customer=123.0),
                       sale(invoice=" 00100 ", customer=" 00123 "),
                       sale(invoice=" c102 ", sku=" 85123a ", customer=None),
                       sale(customer="  ")])
        result = self.policy.normalize_transactions(source)
        self.assertEqual(result.invoice_id.tolist(), ["100", "00100", "C102", "100"])
        self.assertEqual(result.sku.iloc[2], "85123A")
        self.assertEqual(result.customer_id.iloc[:2].tolist(), ["123", "00123"])
        self.assertTrue(result.customer_id.iloc[2:].isna().all())
        self.assertEqual(result.flag_missing_customer_id.tolist(), [False, False, True, True])
        self.assertEqual(result.source_row.tolist(), [2, 3, 4, 5])
        self.assertEqual(len(result), len(source))

    def test_only_original_exact_rows_are_deduplicated_excluding_provenance(self):
        source = rows([sale(), sale(), sale(quantity=4), sale(description="Another description"),
                       sale(invoice=" 100 "), sale(customer="123")])
        source.loc[1, ["source_sheet", "source_row"]] = ["second", 2]
        result = self.policy.normalize_transactions(source)
        self.assertEqual(result.flag_exact_duplicate.tolist(), [True, True, False, False, False, False])
        self.assertEqual(result.flag_duplicate_extra.tolist(), [False, True, False, False, False, False])
        self.assertEqual(result.canonical_record_id.iloc[:2].tolist(), [1, 1])
        self.assertEqual(len(result), 6)
        self.assertEqual(int(result.is_paid_purchase.sum()), 5)
        # Normalizing text and numeric IDs to the same string is not deduplication.
        self.assertEqual(result.customer_id.iloc[0], result.customer_id.iloc[5])
        self.assertTrue(result.is_paid_purchase.iloc[5])

    def test_later_return_does_not_erase_purchase_and_anonymous_purchase_is_retained(self):
        source = rows([sale(date="2010-01-01"),
                       sale(invoice="C200", quantity=-2, date="2011-01-01"),
                       sale(invoice="300", customer=None)])
        result = self.policy.normalize_transactions(source)
        self.assertEqual(result.is_paid_purchase.tolist(), [True, False, True])
        self.assertEqual(result.eligible_personalized_history.tolist(), [True, False, False])
        self.assertEqual(result.eligible_customer_labels.tolist(), [True, False, False])
        self.assertTrue(result.flag_cancellation.iloc[1])
        self.assertTrue(result.flag_nonpositive_quantity.iloc[1])

    def test_special_code_classification_does_not_exclude_alphanumeric_products(self):
        source = rows([sale(sku="POST", description="POSTAGE"),
                       sale(sku="85123A"), sale(sku="mystery", description="Unresolved item"),
                       sale(sku="10002", price=0), sale(sku="PADS", description="PADS TO MATCH ALL CUSHIONS")])
        result = self.policy.normalize_transactions(source)
        self.assertEqual(result.flag_confirmed_non_merchandise.tolist(), [True, False, False, False, False])
        self.assertEqual(result.is_paid_purchase.tolist(), [False, True, True, False, True])
        self.assertTrue(result.flag_unresolved_stock_code.iloc[2])
        self.assertEqual(result.stock_code_status.iloc[4], "reviewed_merchandise")

    def test_missing_or_infinite_measurements_do_not_satisfy_positive_purchase_rule(self):
        source = rows([sale(quantity=None), sale(price="unknown"),
                       sale(quantity=float("inf")), sale(price=float("-inf"))])
        before = source.copy(deep=True)
        result = self.policy.normalize_transactions(source)
        self.assertFalse(result.is_paid_purchase.any())
        self.assertEqual(result.flag_invalid_quantity.tolist(), [True, False, True, False])
        self.assertEqual(result.flag_invalid_price.tolist(), [False, True, False, True])
        self.assertEqual(self.policy.cleaning_statistics(result)["paid_purchases"]["total"], 0)
        pd.testing.assert_frame_equal(source, before)

    def test_only_evidenced_voucher_codes_are_excluded(self):
        source = rows([sale(sku="GIFT_0001_20", description="Dotcomgiftshop Gift Voucher £20.00"),
                       sale(sku="GIFT_0001_60", description=None), sale(sku="M", description="Manual"),
                       sale(sku="S", description="SAMPLES")])
        result = self.policy.normalize_transactions(source)
        self.assertEqual(result.flag_confirmed_non_merchandise.tolist(), [True, False, False, False])
        self.assertEqual(result.flag_unresolved_stock_code.tolist(), [False, True, True, True])
        self.assertEqual(result.is_paid_purchase.tolist(), [False, True, True, True])

    def test_overlapping_flags_and_sequential_funnel_reconcile(self):
        source = rows([sale(), sale(), sale(invoice="C2", quantity=-1, price=0),
                       sale(sku="POST", description="POSTAGE", price=0),
                       sale(quantity=0, price=0), sale(price=-1), sale(customer=None)])
        result = self.policy.normalize_transactions(source)
        report = self.policy.cleaning_statistics(result)
        self.assertEqual(report["funnel"][0]["remaining"], 7)
        self.assertEqual(report["funnel"][-1]["remaining"], 2)
        self.assertEqual(sum(stage["excluded"] for stage in report["funnel"]) + 2, 7)
        self.assertEqual(report["paid_purchases"], {"total": 2, "identified": 1, "anonymous": 1})
        self.assertEqual(report["flag_counts"]["flag_nonpositive_price"], 4)
        self.assertEqual(report["pairwise_flag_overlaps"]["flag_cancellation"]["flag_nonpositive_quantity"], 1)
        self.assertEqual(sum(row["rows"] for row in report["flag_combinations"]), 7)

    def test_cross_sheet_counts_by_date_and_invoice_are_original_row_counts(self):
        source = rows([sale(), sale(), sale(invoice="101", date="2010-12-02"), sale()])
        source.loc[3, ["source_sheet", "source_row"]] = ["second", 2]
        inspection, dates, invoices = self.policy.inspect_overlap(source)
        self.assertEqual(inspection["cross_sheet_exact_duplicates"], {"groups": 1, "all_rows": 3, "extra_rows": 2})
        self.assertEqual(dates.iloc[0]["date"], "2010-12-01")
        self.assertEqual(int(dates.iloc[0]["all_rows"]), 3)
        self.assertEqual(invoices.iloc[0]["invoice_id"], "100")
        self.assertEqual(int(invoices.iloc[0]["extra_rows"]), 2)
        self.assertEqual(inspection["interval_overlaps"][0]["start"], "2010-12-01T00:00:00")

    def test_cli_parquet_outputs_preserve_all_provenance_and_workbook_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            workbook = Path(folder) / "raw.xlsx"
            output = Path(folder) / "generated"
            first = rows([sale(), sale(customer=None), sale(sku="POST", description="POSTAGE")])
            second = rows([sale(), sale(invoice="C100", quantity=-2, date="2011-01-01")])
            with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                first[EXPECTED_COLUMNS].to_excel(writer, sheet_name="first", index=False)
                second[EXPECTED_COLUMNS].to_excel(writer, sheet_name="second", index=False)
            before = hashlib.sha256(workbook.read_bytes()).hexdigest()
            run = subprocess.run([sys.executable, "-m", "scripts.clean_transactions", "--workbook", str(workbook),
                                  "--output-dir", str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            normalized = pd.read_parquet(output / "normalized_transactions.parquet")
            paid = pd.read_parquet(output / "paid_purchases.parquet")
            self.assertEqual(len(normalized), 5)
            self.assertEqual(normalized.source_sheet.tolist(), ["first", "first", "first", "second", "second"])
            self.assertEqual(normalized.source_row.tolist(), [2, 3, 4, 2, 3])
            self.assertEqual(len(paid), 2)
            self.assertEqual(int(paid.customer_id.isna().sum()), 1)
            report = json.loads((output / "cleaning_report.json").read_text())
            self.assertTrue(report["raw_workbook_unchanged"])
            self.assertEqual(report["paid_purchases"]["total"], len(paid))
            self.assertTrue((output / "cleaning_report.md").exists())
            self.assertTrue((output / "cross_sheet_duplicates_by_invoice.csv").exists())
            self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
