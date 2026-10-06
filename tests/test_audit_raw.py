"""Offline checks with deliberately problematic rows; no real data is cleaned."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_raw.py"


class AuditTests(unittest.TestCase):
    def test_download_is_cached_and_conflicting_raw_workbook_is_not_overwritten(self):
        from scripts.audit_raw import prepare_workbook, sha256

        excel = io.BytesIO()
        pd.DataFrame({"Invoice": ["100"]}).to_excel(excel, index=False)
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            archive.writestr("nested/sample.xlsx", excel.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            raw_dir = Path(folder) / "raw"
            with patch("urllib.request.urlopen", return_value=io.BytesIO(zipped.getvalue())) as request:
                workbook, archive = prepare_workbook(raw_dir)
                before = (sha256(workbook), sha256(archive))
                self.assertEqual(prepare_workbook(raw_dir, offline=True), (workbook, archive))
                self.assertEqual((sha256(workbook), sha256(archive)), before)
                self.assertEqual(request.call_count, 1)
                workbook.write_bytes(b"pre-existing conflicting local file")
                conflicting = sha256(workbook)
                with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                    prepare_workbook(raw_dir, offline=True)
                self.assertEqual(sha256(workbook), conflicting)
                self.assertEqual(sha256(archive), before[1])
                self.assertFalse(list(raw_dir.glob("*.part")))

    def test_invalid_download_never_becomes_a_cached_raw_archive(self):
        from scripts.audit_raw import prepare_workbook

        with tempfile.TemporaryDirectory() as folder:
            raw_dir = Path(folder) / "raw"
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"not a ZIP")):
                with self.assertRaises(zipfile.BadZipFile):
                    prepare_workbook(raw_dir)
            self.assertEqual(list(raw_dir.iterdir()), [])

    def test_every_sheet_counts_anomalies_and_preserves_workbook(self):
        columns = ["Invoice", "StockCode", "Description", "Quantity",
                   "InvoiceDate", "Price", "Customer ID", "Country"]
        sale = ["100", "A", "Item A", 2, pd.Timestamp("2010-01-01"), 3, 1, "UK"]
        first = pd.DataFrame([
            sale, sale,
            ["C101", "B", "Item B", -1, pd.Timestamp("2010-01-02"), 0, None, "UK"],
            ["102", "C", "Item C", 0, "invalid", -2, 2, "UK"],
            ["103", "D", "Item D", 1, None, 1, 3, "UK"],
        ], columns=columns)
        second = pd.DataFrame([
            sale,
            ["100", "A", "Item A", 4, pd.Timestamp("2010-01-01"), 3, 1, "UK"],
            ["104", "E", "Item E", 2, pd.Timestamp("2010-01-03"), 4, 4, "UK"],
        ], columns=columns)
        with tempfile.TemporaryDirectory() as folder:
            workbook = Path(folder) / "raw.xlsx"
            output = Path(folder) / "reports"
            with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                first.to_excel(writer, sheet_name="year one", index=False)
                second.to_excel(writer, sheet_name="year two", index=False)
            before = hashlib.sha256(workbook.read_bytes()).hexdigest()
            command = [sys.executable, str(SCRIPT), "--workbook", str(workbook),
                       "--output-dir", str(output)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((output / "raw_audit.json").read_text())
            self.assertEqual(set(report["sheets"]), {"year one", "year two"})
            sheet = report["sheets"]["year one"]
            self.assertEqual(sheet["rows"], 5)
            self.assertEqual(list(sheet["columns"]), columns)
            self.assertEqual(sheet["missing_customer_ids"], 1)
            self.assertEqual(sheet["cancellation_rows"], 1)
            self.assertEqual(sheet["cancellation_invoices"], 1)
            self.assertEqual(sheet["quantity"]["nonpositive"], 2)
            self.assertEqual(sheet["price"]["nonpositive"], 2)
            self.assertEqual(sheet["dates"]["start"], "2010-01-01T00:00:00")
            self.assertEqual(sheet["dates"]["end"], "2010-01-02T00:00:00")
            self.assertEqual(sheet["dates"]["missing"], 1)
            self.assertEqual(sheet["dates"]["unparseable"], 1)
            self.assertEqual(sheet["exact_duplicates"], {"extra_rows": 1, "all_rows": 2, "groups": 1})
            self.assertEqual(report["combined"]["rows"], 8)
            self.assertEqual(report["combined"]["exact_duplicates"]["extra_rows"], 2)
            self.assertEqual(report["combined"]["invoice_stock_repeats"]["extra_rows"], 3)
            self.assertEqual(report["cross_sheet_exact_duplicates"], {"groups": 1, "all_rows": 3})
            self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before)
            rerun = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertEqual(json.loads((output / "raw_audit.json").read_text()), report)
            self.assertIn("year two", (output / "raw_audit.md").read_text())

    def test_absent_expected_fields_are_reported_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            workbook = Path(folder) / "other.xlsx"
            output = Path(folder) / "reports"
            pd.DataFrame({"Unexpected": [1, 1]}).to_excel(workbook, index=False)
            run = subprocess.run([sys.executable, str(SCRIPT), "--workbook", str(workbook),
                                  "--output-dir", str(output)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            sheet = json.loads((output / "raw_audit.json").read_text())["sheets"]["Sheet1"]
            self.assertEqual(sheet["rows"], 2)
            self.assertIsNone(sheet["missing_customer_ids"])
            self.assertIsNone(sheet["dates"])
            self.assertIn("Invoice", sheet["missing_expected_columns"])
            self.assertEqual(sheet["exact_duplicates"]["extra_rows"], 1)


if __name__ == "__main__":
    unittest.main()
