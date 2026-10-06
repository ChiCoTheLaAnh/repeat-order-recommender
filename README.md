# repeat-order-recommender

Starting point for a repeat-first recommendation project. It audits unchanged
UCI Online Retail II data, normalizes transaction rows, and applies the explicit
paid-purchase policy below. It does not build histories, temporal snapshots,
retrieval, models, labels, or APIs.

## Setup and audit

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/). Python 3.12.14 was
used for the verified run below. Run these commands from the repository root:

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked
uv run --locked python scripts/audit_raw.py
```

The script downloads the archive from the
[official UCI endpoint](https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip)
for [Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii).
It retains the ZIP and extracts the workbook into `data/raw/`, inspects **every
sheet**, and writes `outputs/raw_audit.json` and `outputs/raw_audit.md`.
Reports contain aggregates, column types, anomaly counts, definitions, package
versions, and SHA-256 hashes. The archive and workbook are never overwritten.
A cached workbook that differs from its archive causes a failure.

Repeat using the cached data without a download:

```bash
uv run --locked python scripts/audit_raw.py --offline
```

Run the offline validation suite:

```bash
uv run --locked python -m unittest discover -s tests -v
```

Or inspect a workbook you already have; its UCI origin is not assumed:

```bash
uv run --locked python scripts/audit_raw.py --workbook /path/to/online_retail_II.xlsx
```

`--raw-dir` and `--output-dir` override the default locations. Keep custom
locations outside Git or add your own ignore rules. A full workbook audit can
take a few minutes. No credentials or running services are needed. If the cloud
proxy blocks the download, allow `archive.ics.uci.edu` in environment settings,
then rerun. A failed run does not validate old reports left by an earlier run.

## Files

- `pyproject.toml`: minimal Python metadata and pinned pandas, openpyxl, and
  pyarrow dependencies. pandas performs the audit; openpyxl reads Excel.
  pyarrow writes and verifies the normalized and paid-purchase Parquet files.
- `uv.lock`: transitive dependency versions and artifact hashes for `uv sync
  --locked`; keep this file in Git for repeatable installation.
- `scripts/audit_raw.py`: official-source download, verified ZIP extraction,
  cache reuse, per-sheet and combined audits, and Markdown/JSON reporting.
- `tests/test_audit_raw.py`: offline synthetic-workbook checks of anomaly counts,
  cross-sheet duplicates, absent columns, report repeatability, and raw-file
  preservation, plus simulated download/cache checks.
- `scripts/clean_transactions.py`: original-row overlap inspection, nullable-ID
  normalization, policy flags, original-business-row deduplication, paid-purchase
  selection, Parquet roundtrip verification, and a reconciled cleaning report.
- `config/stock_code_policy.json`: reviewed code-level classifications and their
  description evidence. This is an explicit policy, not an authoritative catalog.
- `tests/test_clean_transactions.py`: focused normalization, provenance,
  duplicate-equality, return, anonymous-purchase, classification, overlap/funnel,
  and Parquet/raw-byte preservation tests.
- `.gitignore`: retains the existing Python exclusions and excludes `/data/`
  and `/outputs/`, including downloaded files, reports, and partial downloads.

## What the audit means

Counts describe parsed Excel data rows, excluding headers. Blank Excel cells
are missing; literal strings such as `NA` are retained. Native cell values are
read before dtype inference so text IDs are not silently converted to numbers
just because a particular sheet contains only numeric-looking text. The audit
does not normalize IDs or establish their future domain types.

- Cancellations: invoice text begins with `C`, case-insensitive after trimming
  whitespace. Both affected rows and distinct flagged invoices are reported.
  This convention does not prove that a return can be matched to a purchase.
- Quantities/prices: negative, zero, and nonpositive (`<= 0`) counts, with missing
  and nonnumeric values reported separately. Anomaly counts can overlap.
- Dates: parseable `InvoiceDate` minimum/maximum, missing and unparseable counts.
  No timezone is assumed or assigned.
- Exact duplicates: equality across **all parsed columns**, including missing
  values. `extra_rows` counts repeats beyond the first, `all_rows` counts every
  member, and `groups` counts distinct repeated rows. These are candidates for
  a cleaning decision, not proof that a transaction should be deleted.
- Invoice/product repeats: repeated `(Invoice, StockCode)` pairs with non-null
  keys. Different quantities/prices or repeated legitimate lines can share a
  pair; this is a broader diagnostic, not a deduplication rule.
- Cross-sheet duplicates: exact full rows present in multiple sheets. No rows
  are dropped when computing the combined report. Comparison is unavailable
  if sheet column layouts differ, and that is reported as `null`.

SHA-256 hashes before and after inspection verify byte preservation during the
run. HTTPS and ZIP CRC verification are enabled. The recorded hashes identify
the observed files; they are not independent publisher checksums and do not
authenticate the original source of a manually supplied cache.

## Verified audit results

The official archive was downloaded and every sheet audited in this cloud
environment on October 5, 2026. The cached offline rerun completed after the
cell-type preservation fix. Four offline tests passed, dependency compatibility
passed, and Git exclusions for raw data and reports were checked.

| Metric | Year 2009-2010 | Year 2010-2011 | Combined |
| --- | ---: | ---: | ---: |
| Rows | 525,461 | 541,910 | 1,067,371 |
| Missing customer IDs | 107,927 (20.5395%) | 135,080 (24.9266%) | 243,007 (22.7669%) |
| Missing descriptions | 2,928 | 1,454 | 4,382 |
| Cancellation rows | 10,206 | 9,288 | 19,494 |
| Distinct cancellation invoices | 4,592 | 3,836 | 8,292 |
| Negative quantities | 12,326 | 10,624 | 22,950 |
| Zero quantities | 0 | 0 | 0 |
| Negative prices | 3 | 2 | 5 |
| Zero prices | 3,687 | 2,515 | 6,202 |
| Nonpositive prices | 3,690 | 2,517 | 6,207 |
| Exact duplicate extra rows | 6,865 | 5,268 | 34,335 |
| Exact duplicate member rows | 13,283 | 10,147 | 67,242 |
| Exact duplicate groups | 6,418 | 4,879 | 32,907 |
| Invoice/product repeat extra rows | 13,335 | 10,684 | 45,947 |

Date ranges, without an assigned timezone:

- `Year 2009-2010`: `2009-12-01 07:45:00` to `2010-12-09 20:01:00`.
- `Year 2010-2011`: `2010-12-01 08:26:00` to `2011-12-09 12:50:00`.
- Combined: `2009-12-01 07:45:00` to `2011-12-09 12:50:00`.

Both sheets have the same columns and inferred dtypes:

| Column | Dtype |
| --- | --- |
| Invoice | object |
| StockCode | object |
| Description | object |
| Quantity | int64 |
| InvoiceDate | datetime64[ns] |
| Price | float64 |
| Customer ID | float64 |
| Country | object |

Neither sheet has missing/unparseable dates or missing/nonnumeric quantities
or prices. Aside from customer IDs and descriptions, other columns have no
missing cells. **22,202 exact full-row groups occur in both sheets**, involving
45,046 rows. The sheets' date ranges overlap in December 2010; overlapping
coverage is observed, but the reason for each duplicate is not established.

Raw-file SHA-256 values were identical before and after inspection:

```text
online_retail_ii.zip:  572e36277c2390fbfde10664750731e0a86f55e33470d91919085f0408e67bfb
online_retail_II.xlsx: bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980
```

These are verified measurements of this downloaded snapshot. C-prefix meaning,
transaction validity, identity semantics, and whether a repeated row is a
redundant record remain interpretations for the project owner. No retrieval,
models, API, Docker, or deployment components were added.

## Transaction normalization and cleaning

The cleaning command reads the existing workbook; it does not download or modify
raw data. Run the raw audit first if the workbook has not been downloaded.

```bash
# Inspect overlap and code evidence without writing Parquet files.
uv run --locked python -m scripts.clean_transactions --inspect-only

# Produce both Parquet files and the complete cleaning report.
uv run --locked python -m scripts.clean_transactions

# Run all audit and cleaning tests.
uv run --locked python -m unittest discover -s tests -v
```

`--workbook`, `--output-dir`, and `--stock-policy` override the input workbook,
generated output directory, and reviewed JSON policy. Defaults point to the
existing workbook, `outputs/cleaning/`, and `config/stock_code_policy.json`.
Run with `--inspect-only` before reviewing/changing a code classification, then
rerun the complete command after changing the policy. It leaves any previous
Parquet outputs alone; inspection alone does not refresh those artifacts.

Generated files (all ignored by Git):

- `normalized_transactions.parquet`: **all** original row occurrences, including
  cancellations, anonymous rows, invalid measurements, and duplicate copies.
  `source_sheet` and `source_row` identify the original Excel cell row; header
  is row 1, first data row is 2. `record_id` is 1-based in workbook sheet/row
  order. `canonical_record_id` points to the first identical original business
  row, so duplicate occurrences keep their provenance without double counting.
- `paid_purchases.parquet`: canonical paid-purchase rows, retaining anonymous
  purchasers and unresolved provisional merchandise codes as documented below.
- `cleaning_report.md` / `.json`: original sheet intervals, date overlap, shared
  row counts by date, flags, pairwise intersections, exact flag combinations,
  the sequential count funnel, code evidence, assumptions, and integrity checks.
- `cross_sheet_duplicates_by_date.csv` / `_by_invoice.csv`: complete shared-row
  counts by calendar date and by date/invoice, with distinct full-row groups,
  all occurrence counts, and extra occurrences beyond the first. Normalized
  invoice IDs are used for display/grouping; duplicate equality remains original.
- `overlap_inspection.json`: standalone original-row interval and overlap report.
- `stock_code_inspection.json`: every special code's description frequencies,
  classification reason, row count, and retained paid-purchase count.

### Applied rules and assumptions

Original business equality uses **Invoice, StockCode, Description, Quantity,
InvoiceDate, Price, Customer ID, Country** as native Excel values, including
missing values, **before normalization**. Provenance is excluded. First
occurrence in workbook order is canonical. Duplicate copies remain in the
normalized output, with `flag_exact_duplicate` true for every group member and
`flag_duplicate_extra` true only for later copies. Differences in any business
column keep rows separate. Sharing an invoice/SKU is never enough to collapse
lines, and normalization collisions do not change duplicate equality.

IDs become nullable strings. Native integral numeric cells render without a
`.0` suffix; text is trimmed and leading zeros are preserved. Invoice/SKU text
is uppercased; customer text keeps its case. Literal `NA` text is not a fabricated
missing value. No missing customer IDs are filled. Excel numeric precision
already lost in the source cannot be recovered. Descriptions and countries are
preserved as text, and timestamps have no timezone assigned.

Paid purchases require a canonical, non-cancellation merchandise row with finite
positive quantity and unit price. Missing/nonnumeric/infinite measurements cannot
satisfy positivity and are separately flagged. Missing SKU is separately flagged
and cannot establish merchandise identity. `C`-prefix cancellation detection uses
normalized invoice text. Every exclusion flag remains on the normalized rows.
Missing/unparseable dates become `NaT`; this stage does not add a date exclusion.

Anonymous paid purchases stay in the paid output but both
`eligible_personalized_history` and `eligible_customer_labels` are false.
Identified paid rows have those policy gates set true. These fields do not compute
histories or labels. Returns stay normalized and **never** match/net/erase an
earlier purchase. Missing descriptions are not a purchase exclusion.

Only the explicit reviewed `confirmed_non_merchandise` codes are excluded:
postage/delivery (`POST`, `DOT`, `C2`), fees/commission (`AMAZONFEE`, `BANK CHARGES`,
`CRUK`), adjustments/discount (`ADJUST`, `ADJUST2`, `B`, `D`), explicit test codes
(`TEST001`, `TEST002`), and described voucher codes (`GIFT_0001_10`, `_20`, `_30`,
`_40`, `_50`, `_70`, `_80`). Treating vouchers as value instruments rather than
merchandise is a project policy choice. These code-level rules also apply to
rows with missing descriptions; their evidence and rationale are in the report.

There is **no blanket exclusion of alphanumeric stock codes**. Reviewed `PADS`,
`SP1002`, and selected `DCGS…` codes describe real products. Regular five-digit
codes with up to two letter suffixes are provisional merchandise; that format
alone is not catalog proof. Unreviewed irregular codes are flagged unresolved,
retained as provisional merchandise, and can enter paid purchases if other
criteria pass. `M` (Manual), `S` (SAMPLES), `C3`, generic/undescribed gift codes,
and undescribed/ambiguous `DCGS…` codes remain unresolved rather than being
silently excluded. Review them before interpreting the outputs as catalog-clean.

Flag counts overlap: cancellation can coincide with negative quantity, duplicates,
missing IDs, or non-merchandise. The funnel order is duplicate extras, cancellations,
confirmed non-merchandise, nonpositive quantity, nonpositive price, invalid
quantity, invalid price, missing SKU. Each stage counts exclusions only among
remaining rows; the final count reconciles to the paid output. Missing customer
IDs are an eligibility restriction, not an exclusion. Both Parquet files are
read back and compared to the in-memory frames, and the workbook hash is checked
before and after the complete run.

### Verified cleaning results

All **1,067,371** original occurrences are in the normalized Parquet file.
The paid-purchase output contains **1,004,211** canonical rows: **777,277**
identified and **226,934** anonymous. Anonymous rows are retained with both
personalization/label eligibility gates false. Both Parquet files passed complete
readback comparisons against their in-memory frames. The raw workbook and ZIP
still match the hashes recorded above. All 13 audit/cleaning tests passed.

| Sequential stage | Newly excluded | Remaining |
| --- | ---: | ---: |
| All normalized records | 0 | 1,067,371 |
| Exact duplicate extras | 34,335 | 1,033,036 |
| Cancellations | 19,104 | 1,013,932 |
| Confirmed non-merchandise | 3,745 | 1,010,187 |
| Nonpositive quantity | 3,393 | 1,006,794 |
| Nonpositive price | 2,583 | 1,004,211 |
| Invalid quantity / invalid price / missing SKU | 0 | 1,004,211 |

These sequential exclusions sum to **63,160**. Counts of overlapping flags on
all rows are larger: 19,494 cancellations, 4,377 confirmed non-merchandise,
22,950 nonpositive quantities, and 6,207 nonpositive prices. For example,
19,493 rows have both cancellation and nonpositive-quantity flags; 3,457 have
both nonpositive-quantity and nonpositive-price flags; 7,856 duplicate extras
also lack customer IDs. The generated report contains the full pairwise matrix
and exact flag combinations, including rows with no flags.

The verified sheet-interval intersection is **2010-12-01 08:26:00 through
2010-12-09 20:01:00**, without an assigned timezone. Shared full-row groups
occur on these dates:

| Date | Shared full-row groups | All occurrences | Extra occurrences | Invoices |
| --- | ---: | ---: | ---: | ---: |
| 2010-12-01 | 3,064 | 6,216 | 3,152 | 143 |
| 2010-12-02 | 2,068 | 4,218 | 2,150 | 167 |
| 2010-12-03 | 2,185 | 4,404 | 2,219 | 108 |
| 2010-12-05 | 2,620 | 5,450 | 2,830 | 95 |
| 2010-12-06 | 3,830 | 7,756 | 3,926 | 133 |
| 2010-12-07 | 2,946 | 5,926 | 2,980 | 111 |
| 2010-12-08 | 2,608 | 5,294 | 2,686 | 148 |
| 2010-12-09 | 2,881 | 5,782 | 2,901 | 183 |
| Total | 22,202 | 45,046 | 22,844 | 1,088 |

`extra occurrences` above includes within-sheet repetitions in shared groups.
After removing the **12,133** within-sheet extras, cross-sheet equality accounts
for an additional **22,202** copies; together these give the **34,335** extras
removed from purchase selection. No shared rows were observed on December 4.
This verifies interval intersection and shared record values, but does not
establish why the source sheets contain overlapping records.

All 1,088 affected invoices are reported in
`outputs/cleaning/cross_sheet_duplicates_by_invoice.csv`. Examples:

| Date | Invoice | Shared full-row groups | All occurrences | Extra occurrences |
| --- | --- | ---: | ---: | ---: |
| 2010-12-06 | 537434 | 675 | 1,350 | 675 |
| 2010-12-09 | 538071 | 652 | 1,304 | 652 |
| 2010-12-07 | 537638 | 601 | 1,202 | 601 |

There are **24 unresolved special codes** covering **1,554** original occurrences.
Only `M` (Manual: **851** rows) and `S` (SAMPLES: **3** rows) enter the paid output,
for **854 provisional paid rows**. The other unresolved codes are `C3`, `GIFT`,
`GIFT_0001_60`, `GIFT_0001_90`, and 18 undescribed/ambiguous `DCGS…` codes; all have
zero retained paid rows under the current quantity/price/cancellation rules.
Their exact codes, descriptions, counts, and reasons appear in the cleaning
report and `stock_code_inspection.json`. Their classification remains unresolved
even though none currently contribute paid purchases.

### Unresolved decisions

Review the unresolved code classifications and the provisional-merchandise
assumption, particularly `M` and `S`; revisit voucher treatment if needed.
Later stages must separately decide what constitutes a repeat purchase and how
to handle missing dates and product identity. This implementation does not make
temporal-split, customer-label, return-matching, retrieval, or modeling decisions.
