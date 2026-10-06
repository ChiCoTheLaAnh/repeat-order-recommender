"""Download the official UCI archive and report raw Excel anomalies without cleaning."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

import openpyxl
import pandas as pd
import pyarrow


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
EXPECTED_COLUMNS = ["Invoice", "StockCode", "Description", "Quantity",
                    "InvoiceDate", "Price", "Customer ID", "Country"]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Archive CRC verification failed for {bad}")
        members = [name for name in archive.namelist() if name.lower().endswith(".xlsx")]
        if len(members) != 1:
            raise ValueError(f"Expected one XLSX workbook in archive; found {members}")
        return members[0]


def prepare_workbook(raw_dir, offline=False):
    """Never replace cached raw files; compare extracted bytes before reuse."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive_path = raw_dir / "online_retail_ii.zip"
    if not archive_path.exists():
        if offline:
            raise ValueError("No cached archive. Run without --offline to download from UCI.")
        print(f"Downloading {SOURCE_URL}", flush=True)
        with tempfile.NamedTemporaryFile(dir=raw_dir, suffix=".part", delete=False) as temp:
            temporary = Path(temp.name)
        try:
            request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "repeat-order-raw-audit/0.1"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as target:
                shutil.copyfileobj(response, target)
            validate_archive(temporary)
            os.link(temporary, archive_path)  # Atomic creation; refuses an existing destination.
        finally:
            temporary.unlink(missing_ok=True)
    member = validate_archive(archive_path)
    workbook = raw_dir / Path(member).name
    with tempfile.NamedTemporaryFile(dir=raw_dir, suffix=".part", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with zipfile.ZipFile(archive_path) as archive, archive.open(member) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
        if workbook.exists():
            if sha256(workbook) != sha256(temporary):
                raise ValueError(f"Cached workbook differs from archive: {workbook}. Refusing to overwrite.")
        else:
            os.link(temporary, workbook)
    finally:
        temporary.unlink(missing_ok=True)
    return workbook, archive_path


def duplicate_counts(frame, subset=None):
    members = frame.duplicated(subset=subset, keep=False)
    return {
        "extra_rows": int(frame.duplicated(subset=subset, keep="first").sum()),
        "all_rows": int(members.sum()),
        "groups": int(frame.loc[members].drop_duplicates(subset=subset).shape[0]),
    }


def numeric_counts(series):
    numbers = pd.to_numeric(series, errors="coerce")
    return {
        "negative": int(numbers.lt(0).sum()),
        "zero": int(numbers.eq(0).sum()),
        "nonpositive": int(numbers.le(0).sum()),
        "missing": int(series.isna().sum()),
        "nonnumeric": int((series.notna() & numbers.isna()).sum()),
    }


def summarize(frame):
    result = {
        "rows": len(frame),
        "columns": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "missing_by_column": {str(column): int(count) for column, count in frame.isna().sum().items()},
        "missing_expected_columns": [column for column in EXPECTED_COLUMNS if column not in frame],
        "missing_customer_ids": None,
        "missing_customer_ids_percent": None,
        "cancellation_rows": None,
        "cancellation_invoices": None,
        "quantity": numeric_counts(frame["Quantity"]) if "Quantity" in frame else None,
        "price": numeric_counts(frame["Price"]) if "Price" in frame else None,
        "dates": None,
        "exact_duplicates": duplicate_counts(frame),
        "invoice_stock_repeats": None,
    }
    if "Customer ID" in frame:
        missing = frame["Customer ID"].isna() | frame["Customer ID"].astype("string").str.strip().eq("").fillna(False)
        result["missing_customer_ids"] = int(missing.sum())
        result["missing_customer_ids_percent"] = round(100 * missing.mean(), 4) if len(frame) else None
    if "Invoice" in frame:
        invoices = frame["Invoice"].astype("string").str.strip().str.upper()
        cancelled = invoices.str.startswith("C", na=False)
        result["cancellation_rows"] = int(cancelled.sum())
        result["cancellation_invoices"] = int(invoices[cancelled].nunique())
    if "InvoiceDate" in frame:
        raw_dates = frame["InvoiceDate"]
        dates = pd.to_datetime(raw_dates, errors="coerce", format="mixed")
        result["dates"] = {
            "start": dates.min().isoformat() if dates.notna().any() else None,
            "end": dates.max().isoformat() if dates.notna().any() else None,
            "missing": int(raw_dates.isna().sum()),
            "unparseable": int((raw_dates.notna() & dates.isna()).sum()),
        }
    if {"Invoice", "StockCode"}.issubset(frame.columns):
        eligible = frame.dropna(subset=["Invoice", "StockCode"])
        result["invoice_stock_repeats"] = duplicate_counts(eligible, ["Invoice", "StockCode"])
    return result


def audit_workbook(workbook, archive_path=None):
    inputs = [workbook] + ([archive_path] if archive_path else [])
    before = {str(path): sha256(path) for path in inputs}
    sheets = {}
    frames = []
    with pd.ExcelFile(workbook, engine="openpyxl") as excel:
        for name in excel.sheet_names:
            print(f"Inspecting sheet: {name}", flush=True)
            # Preserve numeric-looking text IDs across sheets; infer only native cell types.
            frame = pd.read_excel(excel, sheet_name=name, dtype=object,
                                  keep_default_na=False, na_values=[""]).infer_objects()
            sheets[name] = summarize(frame)
            frames.append(frame)
    if not frames:
        raise ValueError("Workbook has no sheets")
    combined = pd.concat(frames, ignore_index=True)
    cross_sheet = None
    if all(list(frame.columns) == list(frames[0].columns) for frame in frames):
        # Count rows whose exact full-row value occurs in more than one sheet.
        unique_per_sheet = pd.concat([frame.drop_duplicates() for frame in frames], ignore_index=True)
        shared = unique_per_sheet[unique_per_sheet.duplicated(keep=False)].drop_duplicates()
        involved = combined.merge(shared, how="inner", on=list(combined.columns)) if len(shared) else combined.iloc[:0]
        cross_sheet = {"groups": len(shared), "all_rows": len(involved)}
    after = {str(path): sha256(path) for path in inputs}
    if after != before:
        raise ValueError("A raw file changed during the audit; report not written")
    return {
        "source_url": SOURCE_URL if archive_path else None,
        "provenance_note": "Official UCI endpoint; cached file origin is not independently authenticated. SHA-256 values identify observed bytes, not publisher checksums." if archive_path else "User-supplied local workbook; UCI provenance unverified.",
        "versions": {"python": sys.version.split()[0], "pandas": pd.__version__,
                     "openpyxl": openpyxl.__version__, "pyarrow": pyarrow.__version__},
        "raw_files_sha256": before,
        "raw_files_unchanged": True,
        "sheets": sheets,
        "combined": summarize(combined),
        "cross_sheet_exact_duplicates": cross_sheet,
        "definitions": {
            "rows": "Parsed data rows, excluding the header; no filtering or deduplication.",
            "types": "Pandas infer_objects dtypes from native Excel cell values, not a proposed domain schema. Numeric-looking text is preserved; native numeric IDs may have numeric dtypes.",
            "missing": "Blank Excel cells; literal NA strings retained. Missing customer IDs also include whitespace-only strings.",
            "cancellations": "Invoice text starts with C, case-insensitive after trimming whitespace; a flag, not proof of a matched return.",
            "dates": "Parseable InvoiceDate range; timestamps have no assumed timezone. Missing and unparseable counted separately.",
            "nonpositive": "Numeric values <= 0. Missing/nonnumeric values reported separately; masks can overlap with cancellations.",
            "exact_duplicates": "Equality across every parsed column, including missing values. extra_rows excludes the first; all_rows includes every member; groups counts distinct repeated rows.",
            "invoice_stock_repeats": "Repeated (Invoice, StockCode) among non-null keys. These can be legitimate separate lines, not proven duplicates.",
            "cross_sheet_exact_duplicates": "Full rows occurring in multiple sheets; null if sheet columns differ. Combined dtypes may differ after concatenation.",
        },
    }


def markdown_report(report):
    lines = ["# Raw-data audit", "", report["provenance_note"], "", "No records were cleaned, filtered, deleted, or rewritten.", ""]
    for name, summary in [*report["sheets"].items(), ("Combined (all sheets)", report["combined"])]:
        lines += [f"## {name}", "", f"Rows: **{summary['rows']:,}**", "", "| Column | Inferred dtype | Missing cells |", "| --- | --- | ---: |"]
        for column, dtype in summary["columns"].items():
            lines.append(f"| {column} | {dtype} | {summary['missing_by_column'][column]:,} |")
        lines += ["", "```json", json.dumps({key: value for key, value in summary.items() if key not in {"columns", "missing_by_column"}}, indent=2), "```", ""]
    lines += ["## Cross-sheet exact duplicates", "", "```json", json.dumps(report["cross_sheet_exact_duplicates"], indent=2), "```", "", "## Definitions", ""]
    lines += [f"- **{key}**: {value}" for key, value in report["definitions"].items()]
    lines += ["", "## File integrity and tool versions", "", "```json", json.dumps({key: report[key] for key in ["source_url", "raw_files_sha256", "raw_files_unchanged", "versions"]}, indent=2), "```", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    parser.add_argument("--workbook", type=Path, help="Inspect an existing XLSX without downloading")
    parser.add_argument("--offline", action="store_true", help="Use only cached raw archive/workbook")
    args = parser.parse_args()
    try:
        workbook, archive = (args.workbook, None) if args.workbook else prepare_workbook(args.raw_dir, args.offline)
        report = audit_workbook(workbook.resolve(), archive.resolve() if archive else None)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "raw_audit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (args.output_dir / "raw_audit.md").write_text(markdown_report(report), encoding="utf-8")
        print(f"Audited {report['combined']['rows']:,} rows across {len(report['sheets'])} sheets. Reports: {args.output_dir}")
    except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Audit failed: {error}\nIf the cloud proxy blocks UCI, allow archive.ics.uci.edu in environment settings. No audit results were verified by this run.\n")


if __name__ == "__main__":
    main()
