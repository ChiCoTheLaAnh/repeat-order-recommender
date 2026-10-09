"""Build monthly history-only queries/catalogs and separately matured purchase labels."""

import argparse
import json
from pathlib import Path
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

if __package__:
    from .audit_raw import ROOT, sha256
    from .clean_transactions import markdown_table
else:
    from audit_raw import ROOT, sha256
    from clean_transactions import markdown_table


POLICY_VERSION = "recommendation_v1"
REQUIRED_COLUMNS = ["record_id", "invoice_id", "sku", "customer_id", "invoice_date",
                    "is_paid_purchase", "flag_unresolved_stock_code", "stock_code_status",
                    "eligible_personalized_history", "eligible_customer_labels"]
QUERY_DTYPES = {"cutoff": "datetime64[ns]", "target_end": "datetime64[ns]", "split": "string",
                "query_id": "string", "customer_id": "string", "historical_invoice_count": "int64",
                "historical_sku_count": "int64", "first_purchase_at": "datetime64[ns]",
                "last_purchase_at": "datetime64[ns]", "recency_days": "float64",
                "history_skus": "object", "history_sku_invoice_counts": "object"}
LABEL_DTYPES = {"cutoff": "datetime64[ns]", "target_end": "datetime64[ns]", "split": "string",
                "query_id": "string", "customer_id": "string", "sku": "string",
                "label_type": "string", "target_invoice_count": "int64"}
CATALOG_DTYPES = {"cutoff": "datetime64[ns]", "split": "string", "sku": "string",
                  "historical_invoice_count": "int64", "historical_customer_count": "int64",
                  "anonymous_invoice_count": "int64", "first_seen_at": "datetime64[ns]",
                  "last_seen_at": "datetime64[ns]"}


def monthly_cutoffs():
    return [(cutoff, "train" if cutoff < pd.Timestamp("2011-02-01") else
             "validation" if cutoff < pd.Timestamp("2011-05-01") else "test")
            for cutoff in pd.date_range("2010-03-01", "2011-07-01", freq="MS")]


def typed_frame(frame, dtypes):
    if frame.empty:
        return pd.DataFrame({column: pd.Series(dtype=dtype) for column, dtype in dtypes.items()})
    return frame[list(dtypes)].astype(dtypes).reset_index(drop=True)


def recommendation_eligibility(paid):
    """Add scope flags to a copy; unresolved transactions remain valid cleaning records."""
    missing = set(REQUIRED_COLUMNS).difference(paid.columns)
    if missing:
        raise ValueError(f"Paid cleaning input is missing columns: {sorted(missing)}")
    flagged = paid.copy()
    unresolved = (paid.flag_unresolved_stock_code | paid.stock_code_status.eq("unresolved") |
                  paid.sku.isin(["M", "S"])).fillna(True)
    flagged["recommendation_eligible_v1"] = paid.is_paid_purchase.fillna(False) & ~unresolved
    flagged["recommendation_scope_exclusion"] = pd.Series(pd.NA, index=paid.index, dtype="string")
    flagged.loc[unresolved, "recommendation_scope_exclusion"] = "unresolved_stock_code"
    flagged.loc[~paid.is_paid_purchase.fillna(False), "recommendation_scope_exclusion"] = "not_paid_purchase"
    return flagged


def distribution(values, histogram=True):
    values = pd.Series(values).dropna()
    if values.empty:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None,
                "p90": None, "p95": None, "max": None, "mean": None, "histogram": {} if histogram else None}
    result = {"count": len(values), "min": float(values.min()), "max": float(values.max()), "mean": float(values.mean())}
    for name, quantile in [("p25", .25), ("median", .5), ("p75", .75), ("p90", .9), ("p95", .95)]:
        result[name] = float(values.quantile(quantile))
    result["histogram"] = {str(int(value)): int(count) for value, count in values.value_counts().sort_index().items()} if histogram else None
    return result


def build_snapshot(paid, cutoff, split, observation_end):
    cutoff, observation_end = pd.Timestamp(cutoff), pd.Timestamp(observation_end)
    if cutoff.tz is not None or observation_end.tz is not None or cutoff != cutoff.normalize() or cutoff.day != 1:
        raise ValueError("Use timezone-naive first-of-month midnight cutoffs and a naive observation watermark")
    if pd.isna(observation_end) or observation_end < cutoff:
        raise ValueError("Observation watermark must cover the prediction cutoff")
    target_end = cutoff + pd.offsets.MonthBegin(1)
    labels_mature = observation_end >= target_end
    # Derive eligibility from cleaning fields even if a caller supplies an older derived flag.
    unresolved = (paid.flag_unresolved_stock_code | paid.stock_code_status.eq("unresolved") |
                  paid.sku.isin(["M", "S"])).fillna(True)
    usable = paid.loc[paid.is_paid_purchase.fillna(False) & ~unresolved & paid.invoice_date.notna()]
    history = usable.loc[usable.invoice_date.lt(cutoff)]
    personal = history.loc[history.customer_id.notna() & history.eligible_personalized_history.fillna(False)]
    customers = personal.groupby("customer_id", sort=True).agg(
        historical_invoice_count=("invoice_id", "nunique"), historical_sku_count=("sku", "nunique"),
        first_purchase_at=("invoice_date", "min"), last_purchase_at=("invoice_date", "max"))
    eligible = customers.loc[customers.historical_invoice_count.ge(2) &
                             customers.last_purchase_at.ge(cutoff - pd.DateOffset(days=180))].copy()
    eligible["recency_days"] = (cutoff - eligible.last_purchase_at).dt.total_seconds() / 86400
    by_sku = personal.loc[personal.customer_id.isin(eligible.index)].groupby(["customer_id", "sku"], sort=True).invoice_id.nunique()
    sku_lists = by_sku.reset_index(name="invoice_count").groupby("customer_id", sort=True).agg(
        history_skus=("sku", list), history_sku_invoice_counts=("invoice_count", list))
    queries = eligible.join(sku_lists).reset_index()
    queries["cutoff"], queries["target_end"], queries["split"] = cutoff, target_end, split
    queries["query_id"] = cutoff.strftime("%Y-%m-%d") + "|" + queries.customer_id.astype("string")
    queries = typed_frame(queries, QUERY_DTYPES)

    # The entire visible paid catalog is independent of query-customer eligibility and includes anonymous rows.
    catalog = history.groupby("sku", sort=True).agg(historical_invoice_count=("invoice_id", "nunique"),
              historical_customer_count=("customer_id", "nunique"), first_seen_at=("invoice_date", "min"),
              last_seen_at=("invoice_date", "max"))
    anon_counts = history.loc[history.customer_id.isna()].groupby("sku").invoice_id.nunique()
    catalog["anonymous_invoice_count"] = anon_counts.reindex(catalog.index, fill_value=0)
    catalog = catalog.reset_index()
    catalog["cutoff"], catalog["split"] = cutoff, split
    catalog = typed_frame(catalog, CATALOG_DTYPES)

    labels = typed_frame(pd.DataFrame(), LABEL_DTYPES)
    if labels_mature:
        target = usable.loc[usable.invoice_date.ge(cutoff) & usable.invoice_date.lt(target_end) &
                            usable.customer_id.notna() & usable.eligible_customer_labels.fillna(False) &
                            usable.customer_id.isin(eligible.index)]
        labels = target.groupby(["customer_id", "sku"], sort=True).invoice_id.nunique().reset_index(name="target_invoice_count")
        prior_pairs = pd.MultiIndex.from_frame(personal[["customer_id", "sku"]].drop_duplicates())
        repeated = pd.MultiIndex.from_frame(labels[["customer_id", "sku"]]).isin(prior_pairs)
        known = labels.sku.isin(catalog.sku)
        labels["label_type"] = "outside_catalog"
        labels.loc[known, "label_type"] = "discovery"
        labels.loc[repeated, "label_type"] = "repeat"
        labels["cutoff"], labels["target_end"], labels["split"] = cutoff, target_end, split
        labels["query_id"] = cutoff.strftime("%Y-%m-%d") + "|" + labels.customer_id.astype("string")
        labels = typed_frame(labels, LABEL_DTYPES)
    label_counts = {category: int(labels.label_type.eq(category).sum()) if labels_mature else None
                    for category in ["repeat", "discovery", "outside_catalog"]}
    baskets = labels.groupby("customer_id").sku.nunique().reindex(eligible.index, fill_value=0) if labels_mature else None
    zeros = int(baskets.eq(0).sum()) if labels_mature else None
    report = {"cutoff": cutoff.isoformat(), "target_end": target_end.isoformat(), "split": split,
              "labels_mature": bool(labels_mature), "eligible_customers": len(eligible), "queries": len(queries),
              "catalog_skus": len(catalog), "historical_paid_rows": len(history),
              "historical_anonymous_rows": int(history.customer_id.isna().sum()),
              "queries_no_future_purchases": zeros,
              "fraction_queries_no_future_purchases": zeros / len(queries) if labels_mature and len(queries) else None,
              "label_counts": label_counts, "future_labels": len(labels) if labels_mature else None,
              "fraction_future_labels_outside_catalog": label_counts["outside_catalog"] / len(labels) if labels_mature and len(labels) else None,
              "customer_history_distributions": {column: distribution(queries[column], histogram=column != "recency_days")
                                                 for column in ["historical_invoice_count", "historical_sku_count", "recency_days"]},
              "target_basket_size_distribution": distribution(baskets) if labels_mature else None}
    return queries, labels, catalog, report


ASSUMPTIONS = [
    "Recommendation V1 excludes all unresolved stock codes, including M and S. This is recommendation scope, not transaction invalidity; every cleaning output is retained unchanged. Existing paid/deduplication/identity/anonymous eligibility policies are reused.",
    "Cutoffs are timezone-naive first-of-month midnight. Train: March 2010-January 2011; validation: February-April 2011; test: May-July 2011. History is strictly InvoiceDate < cutoff. Targets are [cutoff, next calendar month), not a fixed number of days.",
    "Eligible customers have at least two distinct historical purchase invoice IDs and a purchase in [cutoff - 180 days, cutoff). History is otherwise all available past purchases, not restricted to the recency window. Missing invoice IDs do not count as distinct invoices.",
    "Known catalog uses all visible, paid, V1-eligible historical purchases, including anonymous purchases and identified customers who do not qualify for a query. No future first/last-seen dates, SKU metadata, or future customer activity enter queries/catalogs.",
    "Anonymous purchases never create queries or personalized labels. Identified rows also honor the existing eligible_personalized_history and eligible_customer_labels gates. Zero-target queries remain in queries.parquet.",
    "Each label is a distinct (cutoff, eligible customer, SKU) purchase in the target month. repeat means in that customer's history; discovery means known in the catalog but new to that customer; outside_catalog means absent from the catalog at cutoff. Frequency uses distinct invoice IDs, not transaction lines.",
    "Unresolved-code exclusions apply to history, catalog, and target labels consistently. Missing/unparseable dates cannot be placed in temporal windows and are reported as unusable; cleaning outputs are not changed.",
    "observation_end is an exclusive coverage watermark. Labels mature only when it reaches the next-month boundary. Immature months keep queries/catalogs but emit no partial labels and have null target metrics, rather than false zero baskets. The watermark must reach the prediction cutoff.",
    "By default the watermark is the last observed timestamp in all paid cleaning rows. This is an explicit coverage assumption, not independent proof of complete ingestion. Supply --observation-end when a known coverage watermark is available. Input dates after an explicit watermark cannot affect a matured earlier target month.",
    "History SKU lists are sorted and paired with distinct-invoice counts. Queries contain history-only features; next-month baskets are stored separately. Reports include zero baskets only for mature months and use customer-SKU label instances as the outside-catalog fraction denominator.",
    "Validation/test metrics are descriptive. The fixed eligibility, recency, stock-code scope, and split policies are not tuned from validation/test outcomes. No candidate generation, ranking, or API is implemented.",
]


def split_statistics(months):
    results = []
    for split in ["train", "validation", "test"]:
        selected = [month for month in months if month["split"] == split]
        mature = [month for month in selected if month["labels_mature"]]
        queries = sum(month["queries"] for month in mature)
        labels = sum(month["future_labels"] for month in mature)
        counts = {category: sum(month["label_counts"][category] for month in mature)
                  for category in ["repeat", "discovery", "outside_catalog"]}
        results.append({"split": split, "months": len(selected), "mature_months": len(mature),
                        "queries": sum(month["queries"] for month in selected), "mature_queries": queries,
                        "future_labels": labels, **counts,
                        "fraction_queries_no_future_purchases": sum(month["queries_no_future_purchases"] for month in mature) / queries if queries else None,
                        "fraction_future_labels_outside_catalog": counts["outside_catalog"] / labels if labels else None})
    return results


def markdown_report(report):
    months = [{**{key: month[key] for key in ["cutoff", "split", "labels_mature", "eligible_customers", "queries", "catalog_skus",
                "fraction_queries_no_future_purchases", "fraction_future_labels_outside_catalog"]}, **month["label_counts"]} for month in report["months"]]
    lines = ["# Monthly temporal snapshot report", "", "## Recommendation scope", "",
             "```json", json.dumps(report["input_counts"], indent=2), "```", "",
             "Unresolved exclusions restrict recommendation V1; they do not invalidate transactions or alter cleaning outputs.", "",
             "## Monthly counts and descriptive outcomes", "", markdown_table(pd.DataFrame(months)), "",
             "## Split totals (query-weighted zero-basket fractions and label-weighted outside-catalog fractions)", "",
             markdown_table(pd.DataFrame(report["splits"])), "",
             "## Per-month distributions", "", "History distributions are over eligible queries. Mature target baskets include zeros; sizes count distinct SKUs.", ""]
    for month in report["months"]:
        lines += ["### " + month["cutoff"][:7], "", "```json",
                  json.dumps({"history": month["customer_history_distributions"], "target_basket_size": month["target_basket_size_distribution"]}, indent=2), "```", ""]
    lines += ["## Policies and assumptions", ""] + ["- " + value for value in ASSUMPTIONS]
    lines += ["", "## Coverage, integrity and artifact checks", "", "```json",
              json.dumps({key: report[key] for key in ["observation_end", "coverage_basis", "cleaning_outputs_unchanged", "cleaning_file_sha256", "cleaning_policy_version", "stock_policy_sha256", "outputs", "versions"]}, indent=2), "```", ""]
    return "\n".join(lines)


def cleaning_hashes(cleaning_dir):
    return {str(path.relative_to(cleaning_dir)): sha256(path) for path in sorted(cleaning_dir.rglob("*")) if path.is_file()}


def write_parquet(frame, path, list_columns=None):
    table = pa.Table.from_pandas(frame, preserve_index=False)
    # Stable nested types also when every snapshot is empty.
    for column, value_type in (list_columns or {}).items():
        index = table.schema.get_field_index(column)
        table = table.set_column(index, column, pa.array(frame[column].tolist(), type=pa.list_(value_type)))
    pq.write_table(table, path, compression="zstd")
    restored = pq.read_table(path)
    if not table.equals(restored, check_metadata=False):
        raise ValueError(f"Parquet roundtrip mismatch: {path}")
    return {"rows": len(frame), "sha256": sha256(path), "roundtrip_verified": True}


def run(cleaning_dir, output_dir, observation_end=None):
    cleaning_dir, output_dir = cleaning_dir.resolve(), output_dir.resolve()
    if output_dir == cleaning_dir or cleaning_dir in output_dir.parents:
        raise ValueError("Snapshot outputs must be outside the cleaning output directory")
    before = cleaning_hashes(cleaning_dir)
    source_report = json.loads((cleaning_dir / "cleaning_report.json").read_text(encoding="utf-8"))
    paid_path = cleaning_dir / "paid_purchases.parquet"
    if before.get(paid_path.name) != source_report["outputs"][paid_path.name]["sha256"]:
        raise ValueError("Paid input does not match its verified cleaning report")
    paid = pd.read_parquet(paid_path)
    if not paid.is_paid_purchase.fillna(False).all() or not paid.record_id.is_unique:
        raise ValueError("Expected canonical paid cleaning records with unique record IDs")
    flagged = recommendation_eligibility(paid)
    eligible = flagged.recommendation_eligible_v1
    valid_dates = paid.invoice_date.notna()
    coverage_basis = "explicit_exclusive_watermark" if observation_end is not None else "assumed_last_observed_paid_timestamp"
    observation_end = pd.Timestamp(observation_end) if observation_end is not None else paid.invoice_date.max()
    if pd.isna(observation_end):
        raise ValueError("No usable invoice dates establish a coverage watermark; supply --observation-end")
    query_parts, label_parts, catalog_parts, months = [], [], [], []
    for cutoff, split in monthly_cutoffs():
        print(f"Building {split} cutoff {cutoff.date()}", flush=True)
        queries, labels, catalog, month = build_snapshot(paid, cutoff, split, observation_end)
        query_parts.append(queries)
        label_parts.append(labels)
        catalog_parts.append(catalog)
        months.append(month)
    queries, labels, catalogs = [pd.concat(parts, ignore_index=True) for parts in [query_parts, label_parts, catalog_parts]]
    sidecar = flagged[["record_id", "sku", "recommendation_eligible_v1", "recommendation_scope_exclusion"]]
    report = {"recommendation_policy_version": POLICY_VERSION, "observation_end": observation_end.isoformat(),
              "coverage_basis": coverage_basis, "months": months, "splits": split_statistics(months), "assumptions": ASSUMPTIONS,
              "input_counts": {"paid_cleaning_rows": len(paid), "excluded_unresolved_rows": int((~eligible).sum()),
                  "excluded_unresolved_by_sku": {str(sku): int(count) for sku, count in paid.loc[~eligible].sku.value_counts().items()},
                  "recommendation_eligible_paid_rows": int(eligible.sum()),
                  "temporally_unusable_eligible_rows": int((eligible & ~valid_dates).sum()),
                  "anonymous_eligible_rows": int((eligible & paid.customer_id.isna()).sum())},
              "cleaning_policy_version": source_report["policy_version"], "stock_policy_sha256": source_report["stock_policy_sha256"],
              "cleaning_file_sha256": before, "cleaning_outputs_unchanged": False,
              "versions": {"python": sys.version.split()[0], "pandas": pd.__version__, "pyarrow": pa.__version__}}
    if cleaning_hashes(cleaning_dir) != before:
        raise ValueError("Cleaning outputs changed while computing snapshots")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, frame in [("queries.parquet", queries), ("labels.parquet", labels), ("catalogs.parquet", catalogs),
                        ("recommendation_eligibility.parquet", sidecar)]:
        lists = {"history_skus": pa.string(), "history_sku_invoice_counts": pa.int64()} if name == "queries.parquet" else None
        outputs[name] = write_parquet(frame, output_dir / name, lists)
    if cleaning_hashes(cleaning_dir) != before:
        raise ValueError("Cleaning outputs changed during snapshot artifact writing")
    report["cleaning_outputs_unchanged"], report["outputs"] = True, outputs
    (output_dir / "snapshot_report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output_dir / "snapshot_report.md").write_text(markdown_report(report), encoding="utf-8")
    print(f"Wrote {len(queries):,} queries, {len(labels):,} labels, and {len(catalogs):,} catalog snapshots; cleaning files unchanged")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleaning-dir", type=Path, default=ROOT / "outputs/cleaning")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/snapshots")
    parser.add_argument("--observation-end", help="Exclusive timezone-naive coverage watermark; defaults to last observed paid timestamp")
    args = parser.parse_args()
    try:
        run(args.cleaning_dir, args.output_dir, args.observation_end)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Snapshot generation failed: {error}\n")


if __name__ == "__main__":
    main()
