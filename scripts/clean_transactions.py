"""Normalize all native Excel rows and derive paid purchases without return netting."""

import argparse
from itertools import combinations
import json
import math
from numbers import Real
from pathlib import Path
import sys

import pandas as pd
import pyarrow
import pyarrow.parquet as pq

if __package__:
    from .audit_raw import EXPECTED_COLUMNS, ROOT, sha256
else:
    from audit_raw import EXPECTED_COLUMNS, ROOT, sha256


DEFAULT_POLICY = ROOT / "config" / "stock_code_policy.json"
CLASSIFICATIONS = ["confirmed_non_merchandise", "reviewed_merchandise", "unresolved"]
EXCLUSION_FLAGS = ["flag_duplicate_extra", "flag_cancellation", "flag_confirmed_non_merchandise",
                   "flag_nonpositive_quantity", "flag_nonpositive_price", "flag_invalid_quantity",
                   "flag_invalid_price", "flag_missing_sku"]
REPORT_FLAGS = EXCLUSION_FLAGS + ["flag_missing_customer_id", "flag_exact_duplicate", "flag_unresolved_stock_code"]


def load_stock_policy(path=DEFAULT_POLICY):
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    known = set()
    for category in CLASSIFICATIONS:
        codes = policy[category]
        if known.intersection(codes):
            raise ValueError("Stock code occurs in multiple classifications")
        if any(code != code.strip().upper() for code in codes):
            raise ValueError("Policy stock codes must be uppercase and trimmed")
        known.update(codes)
    return policy


def normalize_id(value, uppercase=False):
    """Trim text without removing leading zeros; render native integral numbers as integers."""
    if pd.isna(value):
        return pd.NA
    if isinstance(value, Real) and not isinstance(value, bool):
        if not math.isfinite(value):
            return pd.NA
        text = str(int(value)) if value == int(value) else str(value)
    else:
        text = str(value).strip()
    if not text:
        return pd.NA
    return text.upper() if uppercase else text


def id_series(series, uppercase=False):
    return series.map(lambda value: normalize_id(value, uppercase)).astype("string")


def read_workbook(workbook):
    parts = []
    with pd.ExcelFile(workbook, engine="openpyxl") as excel:
        for name in excel.sheet_names:
            print(f"Reading original business rows: {name}", flush=True)
            frame = pd.read_excel(excel, sheet_name=name, dtype=object,
                                  keep_default_na=False, na_values=[""]).infer_objects()
            if set(frame.columns) != set(EXPECTED_COLUMNS):
                raise ValueError(f"Unexpected business columns in sheet {name}: {list(frame.columns)}")
            frame["source_sheet"] = name
            frame["source_row"] = range(2, len(frame) + 2)
            parts.append(frame)
    if not parts:
        raise ValueError("No Excel sheets found")
    return pd.concat(parts, ignore_index=True)


def business_groups(raw):
    return raw.groupby(EXPECTED_COLUMNS, dropna=False, sort=False).ngroup()


def inspect_overlap(raw):
    """Equality is on original native business values, never normalized IDs or provenance."""
    groups = business_groups(raw)
    dates = pd.to_datetime(raw.InvoiceDate, errors="coerce", format="mixed")
    intervals = []
    for name in raw.source_sheet.drop_duplicates():
        part = dates[raw.source_sheet.eq(name)]
        intervals.append({"sheet": str(name), "rows": len(part),
                          "start": part.min().isoformat() if part.notna().any() else None,
                          "end": part.max().isoformat() if part.notna().any() else None})
    overlaps = []
    for first, second in combinations(intervals, 2):
        start = max(first["start"], second["start"]) if first["start"] and second["start"] else None
        end = min(first["end"], second["end"]) if first["end"] and second["end"] else None
        intersects = start is not None and end is not None and start <= end
        overlaps.append({"sheets": [first["sheet"], second["sheet"]],
                         "start": start if intersects else None, "end": end if intersects else None,
                         "intervals_intersect": intersects})
    shared_mask = raw.source_sheet.groupby(groups).transform("nunique").gt(1)
    shared = pd.DataFrame({"group": groups[shared_mask],
                           "date": dates[shared_mask].dt.strftime("%Y-%m-%d").fillna("<missing-or-invalid>"),
                           "invoice_id": id_series(raw.Invoice[shared_mask], uppercase=True).fillna("<missing>"),
                           "extra": raw.duplicated(EXPECTED_COLUMNS)[shared_mask]})
    by_date = shared.groupby("date", sort=True).agg(groups=("group", "nunique"),
                   all_rows=("group", "size"), extra_rows=("extra", "sum"), invoices=("invoice_id", "nunique")).reset_index()
    by_invoice = shared.groupby(["date", "invoice_id"], sort=True).agg(groups=("group", "nunique"),
                   all_rows=("group", "size"), extra_rows=("extra", "sum")).reset_index()
    global_extras = int(raw.duplicated(EXPECTED_COLUMNS).sum())
    within_sheet_extras = int(raw.duplicated(["source_sheet", *EXPECTED_COLUMNS]).sum())
    inspection = {"sheet_intervals": intervals, "interval_overlaps": overlaps,
                  "cross_sheet_exact_duplicates": {"groups": int(shared.group.nunique()),
                      "all_rows": len(shared), "extra_rows": int(shared.extra.sum())},
                  "by_date": by_date.to_dict(orient="records"),
                  "invoice_date_groups": len(by_invoice),
                  "distinct_shared_invoice_ids": int(shared.invoice_id.nunique()),
                  "top_invoice_date_groups": by_invoice.sort_values("all_rows", ascending=False, kind="stable").head(10).to_dict(orient="records"),
                  "duplicate_extra_reconciliation": {"within_sheet_extra_rows": within_sheet_extras,
                      "additional_cross_sheet_extra_rows": global_extras - within_sheet_extras,
                      "combined_extra_rows": global_extras},
                  "verified": "Intervals and original full-row equality are observed. All sheet/row provenance is retained.",
                  "not_established": "Why the sheets share records, whether every shared row represents one real event, or why a repeated invoice/SKU exists. Deduplication is the requested policy.",
                  "count_definition": "groups: distinct shared full rows; all_rows: every occurrence; extra_rows: occurrences beyond the workbook-order first. Includes repeats within a shared group in either sheet."}
    return inspection, by_date, by_invoice


def normalize_transactions(raw, policy=None):
    raw = raw.reset_index(drop=True)
    policy = load_stock_policy() if policy is None else policy
    group = business_groups(raw)
    normalized = pd.DataFrame({
        "record_id": pd.Series(range(1, len(raw) + 1), dtype="int64"),
        "source_sheet": raw.source_sheet.astype("string"),
        "source_row": raw.source_row.astype("int64"),
        "invoice_id": id_series(raw.Invoice, uppercase=True),
        "sku": id_series(raw.StockCode, uppercase=True),
        "customer_id": id_series(raw["Customer ID"]),
        "description": raw.Description.astype("string"),
        "country": raw.Country.astype("string"),
        "quantity": pd.to_numeric(raw.Quantity, errors="coerce"),
        "unit_price": pd.to_numeric(raw.Price, errors="coerce"),
        "invoice_date": pd.to_datetime(raw.InvoiceDate, errors="coerce", format="mixed"),
    })
    normalized["canonical_record_id"] = normalized.record_id.groupby(group).transform("first")
    normalized["flag_exact_duplicate"] = raw.duplicated(EXPECTED_COLUMNS, keep=False)
    normalized["flag_duplicate_extra"] = raw.duplicated(EXPECTED_COLUMNS, keep="first")
    normalized["flag_cancellation"] = normalized.invoice_id.str.startswith("C", na=False)
    normalized["flag_missing_customer_id"] = normalized.customer_id.isna()
    normalized["flag_missing_sku"] = normalized.sku.isna()
    normalized["flag_nonpositive_quantity"] = normalized.quantity.le(0).fillna(False)
    normalized["flag_nonpositive_price"] = normalized.unit_price.le(0).fillna(False)
    # Missing, nonnumeric, or infinite measurements cannot satisfy a positive paid-purchase rule.
    normalized["flag_invalid_quantity"] = normalized.quantity.isna() | normalized.quantity.isin([math.inf, -math.inf])
    normalized["flag_invalid_price"] = normalized.unit_price.isna() | normalized.unit_price.isin([math.inf, -math.inf])
    normalized["flag_confirmed_non_merchandise"] = normalized.sku.isin(policy["confirmed_non_merchandise"])
    reviewed = normalized.sku.isin(policy["reviewed_merchandise"])
    regular = normalized.sku.str.fullmatch(r"\d{5}[A-Z]{0,2}", na=False)
    unresolved = normalized.sku.isin(policy["unresolved"]) | (~regular & ~reviewed & ~normalized.flag_confirmed_non_merchandise & ~normalized.flag_missing_sku)
    normalized["flag_unresolved_stock_code"] = unresolved
    normalized["stock_code_status"] = pd.Series("provisional_merchandise", index=raw.index, dtype="string")
    normalized.loc[reviewed, "stock_code_status"] = "reviewed_merchandise"
    normalized.loc[unresolved, "stock_code_status"] = "unresolved"
    normalized.loc[normalized.flag_confirmed_non_merchandise, "stock_code_status"] = "confirmed_non_merchandise"
    normalized.loc[normalized.flag_missing_sku, "stock_code_status"] = "missing"
    normalized["is_merchandise"] = ~normalized.flag_confirmed_non_merchandise & ~normalized.flag_missing_sku
    normalized["is_paid_purchase"] = ~normalized[EXCLUSION_FLAGS].any(axis=1)
    eligible = normalized.is_paid_purchase & ~normalized.flag_missing_customer_id
    normalized["eligible_personalized_history"] = eligible
    normalized["eligible_customer_labels"] = eligible
    return normalized


def cleaning_statistics(normalized):
    counts = {flag: int(normalized[flag].sum()) for flag in REPORT_FLAGS}
    pairwise = {first: {second: int((normalized[first] & normalized[second]).sum())
                       for second in REPORT_FLAGS} for first in REPORT_FLAGS}
    combinations_frame = normalized.groupby(REPORT_FLAGS, dropna=False, sort=False).size().reset_index(name="rows")
    combinations_list = [{"flags": [flag for flag in REPORT_FLAGS if row[flag]], "rows": int(row["rows"])}
                         for row in combinations_frame.to_dict(orient="records")]
    remaining = pd.Series(True, index=normalized.index)
    funnel = [{"stage": "all_normalized_records", "excluded": 0, "remaining": len(normalized)}]
    for flag in EXCLUSION_FLAGS:
        excluded = int((remaining & normalized[flag]).sum())
        remaining &= ~normalized[flag]
        funnel.append({"stage": "exclude_" + flag.removeprefix("flag_"),
                       "excluded": excluded, "remaining": int(remaining.sum())})
    if not remaining.equals(normalized.is_paid_purchase):
        raise ValueError("Paid purchase mask does not reconcile with the funnel")
    paid = normalized[normalized.is_paid_purchase]
    anonymous = int(paid.flag_missing_customer_id.sum())
    return {"normalized_rows": len(normalized), "distinct_original_business_rows": int((~normalized.flag_duplicate_extra).sum()),
            "flag_counts": counts, "pairwise_flag_overlaps": pairwise,
            "flag_combinations": combinations_list, "funnel": funnel,
            "paid_purchases": {"total": len(paid), "identified": len(paid) - anonymous, "anonymous": anonymous},
            "personalized_history_eligible": int(normalized.eligible_personalized_history.sum()),
            "customer_labels_eligible": int(normalized.eligible_customer_labels.sum()),
            "unresolved_paid_purchases": int(paid.flag_unresolved_stock_code.sum()),
            "funnel_definition": "Sequential exclusions on remaining rows; sums reconcile. Flag counts and combinations describe all original occurrences and are not additive exclusions. Missing customer IDs are not a paid-purchase exclusion."}


def inspect_stock_codes(normalized, policy):
    regular = normalized.sku.str.fullmatch(r"\d{5}[A-Z]{0,2}", na=False)
    special = normalized[~regular | normalized.flag_unresolved_stock_code | normalized.flag_confirmed_non_merchandise]
    records = []
    reasons = {code: reason for category in CLASSIFICATIONS for code, reason in policy[category].items()}
    for code, frame in special.groupby("sku", dropna=False, sort=True):
        descriptions = frame.description.value_counts(dropna=False)
        records.append({"sku": None if pd.isna(code) else str(code), "rows": len(frame),
                        "status": str(frame.stock_code_status.iloc[0]),
                        "reason": reasons.get(code, "No reviewed catalog evidence; retained provisionally, not excluded by code format."),
                        "paid_rows_after_deduplication": int(frame.is_paid_purchase.sum()),
                        "positive_non_cancellation_rows_before_deduplication": int((~frame.flag_cancellation & frame.quantity.gt(0) & frame.unit_price.gt(0)).sum()),
                        "descriptions": [{"description": None if pd.isna(description) else str(description), "rows": int(count)}
                                         for description, count in descriptions.items()]})
    return records


def markdown_table(frame):
    if frame.empty:
        return "(none)"
    header = "| " + " | ".join(map(str, frame.columns)) + " |"
    separator = "| " + " | ".join(["---"] * len(frame.columns)) + " |"
    rows = ["| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in row) + " |"
            for row in frame.itertuples(index=False, name=None)]
    return "\n".join([header, separator, *rows])


def markdown_report(report):
    inspection = report["overlap_inspection"]
    lines = ["# Transaction cleaning report", "", "## Verified original sheet overlap", "",
             markdown_table(pd.DataFrame(inspection["sheet_intervals"])), "",
             "```json", json.dumps(inspection["interval_overlaps"], indent=2), "```", "",
             inspection["verified"], "", "Not established: " + inspection["not_established"], "",
             "Cross-sheet duplicate totals: " + json.dumps(inspection["cross_sheet_exact_duplicates"]), "",
             inspection["count_definition"], "", "### Shared rows by date", "",
             markdown_table(pd.DataFrame(inspection["by_date"])), "",
             "Duplicate-extra reconciliation (additional cross-sheet copies exclude duplicates already counted within each sheet): " + json.dumps(inspection["duplicate_extra_reconciliation"]), "",
             "### Largest shared invoice/date groups", "",
             markdown_table(pd.DataFrame(inspection["top_invoice_date_groups"])), "",
             "Every affected invoice is listed in cross_sheet_duplicates_by_invoice.csv (date + normalized display invoice; duplicate equality uses original values).", "",
             "## Reconciled row-count funnel", "", markdown_table(pd.DataFrame(report["funnel"])), "",
             report["funnel_definition"], "", "Paid purchases: " + json.dumps(report["paid_purchases"]), "",
             "## Overlapping flags on all normalized records", "",
             markdown_table(pd.DataFrame(report["flag_counts"].items(), columns=["flag", "rows"])), "",
             "### Pairwise intersections", "", markdown_table(pd.DataFrame(report["pairwise_flag_overlaps"]).rename_axis("flag").reset_index()), "",
             "### Exact flag combinations (sum to all normalized rows)", "",
             markdown_table(pd.DataFrame([{"flags": ", ".join(row["flags"]) or "none", "rows": row["rows"]} for row in report["flag_combinations"]])), "",
             "## Reviewed and unresolved special stock codes", "",
             "Complete description evidence and frequencies are in stock_code_inspection.json. Classification is an explicit policy, not a product catalog guarantee.", "",
             markdown_table(pd.DataFrame([{key: entry[key] for key in ["sku", "status", "rows", "paid_rows_after_deduplication", "reason"]}
                                         for entry in report["stock_code_inspection"]])), "",
             "## Policy and assumptions", ""]
    lines += ["- " + assumption for assumption in report["assumptions"]]
    lines += ["", "## Integrity and output validation", "", "```json",
              json.dumps({key: report[key] for key in ["raw_workbook_sha256", "raw_workbook_unchanged", "stock_policy_sha256", "versions", "outputs"]}, indent=2), "```", ""]
    return "\n".join(lines)


ASSUMPTIONS = [
    "All original business-row occurrences are kept in normalized_transactions.parquet, with workbook-order record_id, source_sheet, and 1-based Excel source_row (header is row 1).",
    "Equality uses all eight original native business columns before normalization, including missing values, excluding provenance. First occurrence in workbook sheet/row order is canonical. Only canonical paid rows enter paid_purchases.parquet; canonical_record_id links repeated occurrences.",
    "IDs are nullable strings. Native integral numeric cells lose their .0 suffix; text is trimmed, text leading zeros preserved. Invoice/SKU text is uppercased, customer text keeps its case. No missing customer IDs are fabricated. Numeric Excel precision cannot be recovered.",
    "Invoice/SKU alone is never a duplicate key. Different quantities, prices, descriptions, dates, countries, or customer IDs remain distinct original business rows even if normalized IDs coincide.",
    "Paid purchases require a canonical row, no C-prefix cancellation, a present provisional/reviewed merchandise SKU, and finite positive quantity and unit price. Missing/nonnumeric/infinite measurements cannot satisfy positivity. Dates retain no assumed timezone; missing/unparseable dates become NaT and do not add a paid-purchase exclusion.",
    "Anonymous paid purchases are retained but eligible_personalized_history and eligible_customer_labels are false. Eligibility columns are policy gates only; no customer history, temporal snapshots, or labels are computed.",
    "Returns/cancellations remain in the normalized output. No matching, quantity netting, or retroactive removal of an earlier purchase is performed.",
    "Only explicitly reviewed confirmed_non_merchandise codes are excluded. Reviewed descriptions support postage/fees/adjustments/discounts/test-entry classifications. Voucher exclusion is a stated policy choice treating gift vouchers as financial value instruments.",
    "Five-digit codes with up to two letter suffixes are provisional merchandise, not catalog-confirmed products. Reviewed PADS, SP1002, and selected DCGS codes are merchandise. No blanket alphanumeric exclusion is used.",
    "Unresolved special codes (including M and S) are retained as provisional merchandise if other paid criteria pass, and flagged. Code-level classifications also apply to rows whose descriptions are missing; reasons/evidence are reported for review.",
    "Aggregate flag counts overlap. The ordered funnel excludes each row once; original-row pairwise intersections and exact flag combinations expose overlap rather than pretending counts are additive.",
]


def run(workbook, output_dir, policy_path=DEFAULT_POLICY, inspect_only=False):
    workbook = workbook.resolve()
    output_dir = output_dir.resolve()
    output_names = ["normalized_transactions.parquet", "paid_purchases.parquet", "cleaning_report.json",
                    "cleaning_report.md", "cross_sheet_duplicates_by_date.csv",
                    "cross_sheet_duplicates_by_invoice.csv", "overlap_inspection.json", "stock_code_inspection.json"]
    if workbook in [(output_dir / name).resolve() for name in output_names]:
        raise ValueError("Generated output would overwrite the raw workbook")
    before = sha256(workbook)
    raw = read_workbook(workbook)
    inspection, by_date, by_invoice = inspect_overlap(raw)
    policy = load_stock_policy(policy_path)
    normalized = normalize_transactions(raw, policy)
    stock_inspection = inspect_stock_codes(normalized, policy)
    if sha256(workbook) != before:
        raise ValueError("Raw workbook changed during reading/inspection")
    output_dir.mkdir(parents=True, exist_ok=True)
    by_date.to_csv(output_dir / "cross_sheet_duplicates_by_date.csv", index=False)
    by_invoice.to_csv(output_dir / "cross_sheet_duplicates_by_invoice.csv", index=False)
    (output_dir / "overlap_inspection.json").write_text(json.dumps(inspection, indent=2) + "\n", encoding="utf-8")
    (output_dir / "stock_code_inspection.json").write_text(json.dumps(stock_inspection, indent=2) + "\n", encoding="utf-8")
    if inspect_only:
        print(f"Inspection reports written to {output_dir}; no Parquet outputs written")
        return inspection
    paid = normalized.loc[normalized.is_paid_purchase].copy()
    report = cleaning_statistics(normalized)
    report.update({"overlap_inspection": inspection, "stock_code_inspection": stock_inspection,
                   "assumptions": ASSUMPTIONS, "raw_workbook_sha256": before,
                   "raw_workbook_unchanged": True, "stock_policy_sha256": sha256(Path(policy_path)),
                   "policy_version": policy["policy_version"],
                   "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "pyarrow": pyarrow.__version__}})
    outputs = {}
    for name, frame in [("normalized_transactions.parquet", normalized), ("paid_purchases.parquet", paid)]:
        path = output_dir / name
        frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
        if pq.read_metadata(path).num_rows != len(frame):
            raise ValueError(f"Parquet row count mismatch: {path}")
        # Read back the complete artifact; check values/dtypes as well as metadata row counts.
        restored = pd.read_parquet(path, engine="pyarrow")
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), restored.reset_index(drop=True))
        outputs[name] = {"rows": len(frame), "sha256": sha256(path), "roundtrip_verified": True}
    report["outputs"] = outputs
    if sha256(workbook) != before:
        raise ValueError("Raw workbook changed during normalization/output; cleaning report not written")
    (output_dir / "cleaning_report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output_dir / "cleaning_report.md").write_text(markdown_report(report), encoding="utf-8")
    print(f"Normalized {len(normalized):,} rows; retained {len(paid):,} paid purchases. Raw workbook unchanged. Reports: {output_dir}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=ROOT / "data/raw/online_retail_II.xlsx")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/cleaning")
    parser.add_argument("--stock-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--inspect-only", action="store_true", help="Write overlap and stock-code reports without Parquet files")
    args = parser.parse_args()
    try:
        run(args.workbook, args.output_dir, args.stock_policy, args.inspect_only)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Normalization failed: {error}\n")


if __name__ == "__main__":
    main()
