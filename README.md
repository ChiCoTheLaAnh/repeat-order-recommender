# repeat-order-recommender

Starting point for a repeat-first recommendation project. It audits unchanged
UCI Online Retail II data, normalizes transaction rows, and applies the explicit
paid-purchase policy below. Recommendation V1 adds monthly history snapshots and
next-month purchase labels. It now implements past-only candidate retrieval and
training-only heuristic evaluation. Milestone 2 adds pointwise ranking models
and temporal validation. Milestone 3 adds an immutable historical release and
local FastAPI inference. The test split remains unopened and unevaluated by
Milestones 2 and 3; no cloud deployment is implemented.

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

- `pyproject.toml`: Python metadata and pinned pandas, openpyxl, pyarrow, and
  scipy dependencies. pandas performs the audit; openpyxl reads Excel.
  pyarrow writes Parquet; scipy supplies sparse behavioral similarity.
  Milestone 2 pins scikit-learn, the CPU-only XGBoost distribution, and joblib.
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
- `scripts/temporal_snapshots.py`: recommendation scope eligibility and fixed
  monthly history-only query/catalog snapshots, matured next-month labels, and
  descriptive split/month reports. Reads existing cleaning outputs only.
- `tests/test_temporal_snapshots.py`: cutoff/180-day boundaries, anonymous catalog
  versus query/label handling, distinct-invoice frequency, label maturity,
  future-data invariance, fixed splits, empty outputs, and cleaning preservation.
- `config/retrieval_policy_v1.json`: initial source weights, retrieval windows,
  tie-breaking, ranker choices, metric populations/denominators, and ablation
  definitions, recorded before the first training retrieval evaluation.
- `scripts/retrieval.py`: past-only indexes, safe binary cosine neighbors,
  deterministic interleaving, heuristic rankers, and target/oracle metrics.
- `scripts/training_baselines.py`: training-only snapshot reader, candidate and
  standalone recommendations, four source ablations, integrity checks, and
  monthly/pooled baseline reports. No held-out evaluation switch is provided.
- `tests/test_retrieval.py` and `tests/test_training_baselines.py`: hand-calculated
  metrics/oracles, duplicate/tie handling, similarity limits and overflow,
  exact windows, seed normalization, future/label invariance, standalone catalog
  evidence, segment populations, and split/maturity guards.
- `scripts/ranking_features.py`: one-cutoff past-only feature generation, ordered
  numeric matrices, candidate-label joins, equal query weights and temporal guards.
- `scripts/ranking_models.py`: training-only preprocessing, weighted logistic
  regression/XGBoost, raw-margin batch scoring, and exact-reload local artifacts.
- `scripts/ranking_evaluation.py`: unchanged V1 metric helpers, identical-pool
  heuristic/model comparison, past-only segments, paired customer-cluster
  bootstrap, fixed selection rules and illustrative error cases.
- `scripts/milestone2.py`: one-command feature/train/validation orchestration,
  bounded Parquet reads, unchanged-policy checks, reports and JSON run manifests.
- `config/ranking_features_v1.json` and `config/ranking_models_v1.json`: versioned
  feature order/windows/missing values, transformations, three predefined XGBoost
  settings, fixed seeds, segments, bootstrap and advancement thresholds.
- `tests/test_ranking_features.py`, `tests/test_ranking_models.py`, and
  `tests/test_milestone2.py`: feature/interval/future checks, labels/weights,
  training-only preprocessing, exact model reload, ranking ties, cluster
  intervals, model-selection boundaries, error-case coverage and actual read
  filters in a small end-to-end run.
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
Recommendation V1 excludes unresolved codes from its own scope as documented
below, while leaving the cleaning classifications and files intact. Return
matching and any later product/catalog classification changes remain separate
decisions.

## Recommendation V1 temporal snapshots and labels

Run after the cleaning artifacts exist:

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv run --locked python -m scripts.temporal_snapshots
uv run --locked python -m unittest discover -s tests -v
```

`--cleaning-dir` defaults to `outputs/cleaning/`; `--output-dir` defaults to
`outputs/snapshots/`. The output directory must be outside the cleaning directory.
The paid file must match the SHA-256 in its cleaning report. Every file under
the cleaning directory is hashed before/after the run; none is rewritten.
The raw workbook is not read or altered by this stage. Generated snapshot files
remain excluded from Git under `/outputs/`.

Recommendation eligibility is an additional **scope policy**, not transaction
invalidity: V1 excludes all rows flagged/classified as unresolved stock codes,
including `M` and `S`. Alphanumeric products already eligible under the cleaning
policy stay eligible. The cleaning paid-purchase file still contains the
unresolved transactions. A separate `recommendation_eligibility.parquet` sidecar
records the V1 decision and reason against each paid `record_id`.

### Fixed time windows

Cutoffs are first-of-month midnight, timezone-naive like the source timestamps:

| Split | Prediction cutoffs | Target months |
| --- | --- | --- |
| Train | March 2010–January 2011 (11) | March 2010–January 2011 |
| Validation | February–April 2011 (3) | February–April 2011 |
| Test | May–July 2011 (3) | May–July 2011 |

For cutoff `t`, history uses only paid, V1-eligible purchases with
`invoice_date < t`. Eligible customers have at least **two distinct historical
purchase invoice IDs** and a purchase in **[t − 180 days, t)**. The 180 days
restrict recent activity, not the total history used for counts and repeat
membership. Purchase frequency counts distinct invoice IDs, not transaction
rows or quantities. Missing invoice IDs cannot contribute distinct invoices.

The known catalog uses **all** V1-eligible historical paid purchases, including
anonymous purchases and purchases by identified customers who do not qualify
for a query. Anonymous rows never create customer queries or personalized labels.
Existing cleaning history/label eligibility gates are also respected; no
customer identities are filled or invented. Records with missing/unparseable
dates cannot enter temporal windows and are counted separately in the report.

Targets use **[t, next_calendar_month)**. The next-month boundary itself is
excluded; the horizon is a calendar month, not 30 days. For every eligible
customer, labels are distinct purchased SKUs, partitioned into:

- `repeat`: previously purchased by that customer in visible history.
- `discovery`: in the known catalog at `t`, but new to that customer.
- `outside_catalog`: absent from the known catalog at `t`.

Recommendation scope applies to target purchases too. No queries are selected
based on future purchasing: eligible customers with zero next-month purchases
remain in `queries.parquet`. No returns are matched or retroactively netted.

### Label maturity and coverage assumption

An **exclusive observation watermark** must reach the next-month boundary
before a target month is labeled mature. Immature months retain queries/catalogs,
emit no partial labels, and have `null` target metrics rather than being called
zero-purchase months. The watermark must reach each prediction cutoff.

The default watermark is the last observed timestamp in the paid cleaning input.
This is an explicit coverage assumption; seeing that timestamp does not prove
complete source ingestion. With a known complete-through boundary, provide it:

```bash
uv run --locked python -m scripts.temporal_snapshots --observation-end '2011-08-01'
```

For the requested July 2011 cutoff, labels mature at August 1, 2011. The current
input extends through December 9, 2011, so every requested target month is mature
under the stated assumption. Query histories and catalogs never depend on this
future coverage metadata or target outcomes.

### Generated snapshot files

All monthly snapshots are combined into three Parquet files, keyed by `cutoff`
and carrying `split`; filter those columns to read a particular month:

- `queries.parquet`: one `(cutoff, customer_id)` query for every eligible customer,
  including zero-target customers. `query_id` links labels. Features include
  historical distinct-invoice/SKU counts, first/last purchase timestamps, recency,
  sorted `history_skus`, and aligned `history_sku_invoice_counts`. These are
  history-only features: no target-basket statistics are stored in query rows.
- `labels.parquet`: one `(cutoff, customer_id, sku)` target label, its category,
  and target-month distinct-invoice frequency. Each label links to a query.
  Immature targets have no label rows; consult the report's maturity status.
- `catalogs.parquet`: one `(cutoff, sku)` visible catalog entry, with historical
  distinct-invoice/customer counts, anonymous invoice counts, and historical
  first/last-seen timestamps. No future SKU metadata is used.
- `recommendation_eligibility.parquet`: all paid input record IDs, SKUs, scope
  eligibility, and exclusion reason; it does not replace cleaning artifacts.
- `snapshot_report.md` / `.json`: per-month eligible customers/queries, catalog
  sizes, zero-target fractions, repeat/discovery/outside-catalog label counts,
  outside-catalog fractions, history and distinct-SKU basket distributions,
  coverage assumptions, input hashes, and complete Parquet readback checks.

History distributions cover eligible queries only. Mature target-basket
distributions include zeros and count distinct SKUs across the entire month,
not invoice lines. Outside-catalog fractions use customer-SKU label instances
as the denominator. Split totals pool query instances across months: a customer
may contribute multiple queries. Split fractions are weighted by queries or
labels, not an unweighted average of monthly percentages. Zero denominators are
reported as `null`.

Appending or changing transactions dated at/after a cutoff can change its
labels, but cannot change its history features, customer eligibility, or known
catalog. Validation/test results are descriptive: the fixed rules above are
not tuned using those outcomes. The following stage evaluates retrieval and
heuristic ranking on training cutoffs only.

### Verified snapshot results

The run read **1,004,211** canonical paid cleaning rows. Recommendation V1
excluded **854** unresolved rows (`M`: 851; `S`: 3), leaving **1,003,357**
eligible paid rows, including **226,761 anonymous rows** for catalog evidence.
No eligible rows had unusable dates. All cleaning-file hashes remained identical.

All **17** requested target months are mature under the documented default
watermark (`2011-12-09 12:50:00`). Outputs contain **33,960 customer-month queries**,
**312,873 customer-SKU labels**, and **66,881 cutoff-SKU catalog entries**.
All four Parquet files passed complete Arrow readback checks. The 22-test suite
passed with deprecation warnings treated as errors. Rebuilding every cutoff
from input truncated strictly before that cutoff reproduced its queries and
catalogs exactly, verifying future-data invariance on the actual data as well
as the synthetic tests.

These are descriptive outcomes under the fixed policies, not model metrics or
evidence used to tune validation/test policy:

| Split | Queries | Repeat labels | Discovery labels | Outside-catalog labels | Zero-target queries | Outside-catalog fraction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 19,320 | 93,521 | 106,657 | 6,549 | 64.28% | 3.17% |
| Validation | 7,702 | 24,728 | 22,479 | 1,304 | 76.36% | 2.69% |
| Test | 6,938 | 30,111 | 23,952 | 3,572 | 69.30% | 6.20% |

Monthly customers and queries are identical because each eligible customer
creates exactly one query at each cutoff:

| Cutoff | Split | Customers / queries | Known SKUs | Zero-target queries | Outside-catalog fraction |
| --- | --- | ---: | ---: | ---: | ---: |
| 2010-03-01 | Train | 703 | 3,351 | 48.51% | 8.47% |
| 2010-04-01 | Train | 1,005 | 3,556 | 55.62% | 3.03% |
| 2010-05-01 | Train | 1,228 | 3,600 | 60.75% | 4.57% |
| 2010-06-01 | Train | 1,479 | 3,671 | 59.36% | 0.53% |
| 2010-07-01 | Train | 1,650 | 3,722 | 64.36% | 3.55% |
| 2010-08-01 | Train | 1,800 | 3,781 | 66.44% | 3.14% |
| 2010-09-01 | Train | 1,915 | 3,820 | 62.45% | 6.96% |
| 2010-10-01 | Train | 2,045 | 3,913 | 59.56% | 4.24% |
| 2010-11-01 | Train | 2,301 | 4,016 | 57.32% | 1.31% |
| 2010-12-01 | Train | 2,577 | 4,073 | 73.15% | 0.80% |
| 2011-01-01 | Train | 2,617 | 4,090 | 77.15% | 0.56% |
| 2011-02-01 | Validation | 2,595 | 4,098 | 78.69% | 1.61% |
| 2011-03-01 | Validation | 2,569 | 4,120 | 73.92% | 3.10% |
| 2011-04-01 | Validation | 2,538 | 4,160 | 76.44% | 3.17% |
| 2011-05-01 | Test | 2,443 | 4,216 | 68.73% | 9.11% |
| 2011-06-01 | Test | 2,217 | 4,292 | 68.83% | 7.31% |
| 2011-07-01 | Test | 2,278 | 4,402 | 70.37% | 1.78% |

The monthly outside-catalog column is a **fraction of distinct customer-SKU
labels**, not a count. Complete label counts and distribution histograms are in
`outputs/snapshots/snapshot_report.md` and `.json`. For example, March 2010
eligible histories have median 3 invoices and 41 SKUs; July 2011 histories have
median 6 invoices and 76 SKUs. July target baskets have median 0 and 90th
percentile 27 distinct SKUs, with zero-target customers retained in that
distribution. No threshold or scope policy was changed in response to these
results.

## Candidate retrieval and training-only baselines

Reuse existing cleaning and snapshot artifacts:

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked
uv run --locked python -m scripts.training_baselines
uv run --locked python -W error::DeprecationWarning -m unittest discover -s tests -v
```

The runner evaluates **only March 2010–January 2011 training cutoffs**. Snapshot
queries, labels, and catalogs are read with a `split=train` filter. Validation
and test retrieval/ranking remain unevaluated. There is no ML training or API.
`--cleaning-dir`, `--snapshot-dir`, `--output-dir`, and `--policy` override paths;
outputs default to `outputs/retrieval_v1/`, outside both input directories.

### Frozen initial policy

`config/retrieval_policy_v1.json` records the definitions before the first
training retrieval evaluation. Its SHA-256 and complete contents appear in the
report. No policy was adjusted using these outcomes. Future held-out evaluation
must use an explicitly frozen policy and metric definition.

At each cutoff, indexes are rebuilt exclusively from V1-eligible paid purchases
**strictly before the cutoff**. The existing snapshot catalog must equal that
past-only catalog. Query SKU histories and distinct-invoice counts must also
match the cleaning-derived history. No future positives are added to candidates.
Anonymous purchases contribute catalog evidence only, never popularity,
similarity, customer history, or personalized queries/labels. Identified
customers need not qualify for a query to contribute popularity or similarity.

Sources and initial interleaving shares:

| Source | Score / ordering | Share |
| --- | --- | ---: |
| Prior purchases | `log1p(distinct invoice count) * exp(-elapsed days / 90)` over lifetime visible history | 50% |
| Behavioral neighbors | Binary identified customer–SKU cosine over `[t-180 days,t)`; at least 5 shared purchasers; no self-neighbors; top 50 per SKU | 30% |
| Recent popularity | Distinct identified purchasers over `[t-30 days,t)` | 15% |
| Longer popularity | Distinct identified purchasers over `[t-180 days,t)` | 5% |

Neighbor seeds are the customer's ten most recently purchased distinct SKUs
from lifetime pre-cutoff history. Seed weights are their history scores divided
by their sum; a zero sum uses uniform weights. Destination scores sum weighted
cosines across seeds. Already purchased destinations remain possible; only
self-neighbor edges are removed. Binary matrix values and shared-purchaser
intersections use **int64**, and cosine denominator products use float64.

Interleaving repeatedly selects the active source with the smallest
`(unique emissions + 1) / share`, comparing integer cross-products exactly.
Ties use the recorded source order. Duplicate heads are skipped without
consuming an emission; exhausted sources are removed, allowing remaining
sources to backfill. Stop at 200 distinct SKUs or complete exhaustion. Shares
are initial scheduling weights, not guaranteed quotas when sources overlap or
exhaust. The same ordered list supplies prefixes of 50, 100, and 200. A selected
source records the emission; membership flags independently record every source
that supplied that SKU. Within-source score ties use ascending normalized SKU.
Seed recency ties use that same lexical rule.

The four ablations each omit one source, using the remaining sources and the
same merge rule. Remaining scheduling weights implicitly redistribute its
share. They also retain the same prefix, ranker, and metric definitions.

### Baselines and evaluation definitions

Both rankers receive each **identical prefix pool**: recent popularity orders
by descending 30-day distinct purchasers; personalized recency/frequency puts
previously purchased items first by history score, then orders never-purchased
items by recent popularity. All ties use SKU. The interleaving order itself is
also reported as a descriptive baseline.

Both baselines additionally produce standalone top-10 lists from the **entire
known catalog**, without candidate restrictions. Catalog-only items with zero
identified popularity remain available there, using lexical tie-breaking. This
can include anonymous-only catalog evidence. These lists make a stronger
unrestricted baseline visible rather than hiding it behind retrieval misses.

The primary target is the distinct union of snapshot `repeat` and `discovery`
labels; `outside_catalog` labels are diagnostic only. Primary macro averages
include queries with at least one known-catalog target. The report gives their
count/fraction, zero-purchase queries, and queries with only outside-catalog
purchases. Queries excluded from target metrics remain in recommendations,
shortlist distributions, coverage, and `query_diagnostics.parquet`.

- Candidate Recall@50/100/200 divides distinct retrieved positives by the
  **full known-catalog target count**, with separate repeat/discovery recall.
- Recall@10 uses the same target denominator after ranking. Segment recall
  averages only queries with that nonempty repeat/discovery target segment.
- Binary NDCG@10 discounts a hit at rank `r` by `1/log2(r+1)`. Its ideal
  denominator places `min(10, full known-catalog target count)` hits first,
  including positives missed by retrieval. It is never renormalized to the pool.
- Each pool's oracle places `min(10, retrieved positive count)` hits first,
  retaining the same full-target denominators for NDCG@10 and Recall@10.
  Oracle computations are evaluation only; they never affect candidate order.
- Empty candidates/recommendations score zero for a nonempty target. Empty
  target segments have `null` metrics and are excluded from that segment's macro
  average. The JSON reports the query denominator for every metric.
- Coverage is each cutoff's union of returned SKUs across **all** queries,
  divided by its known catalog size: candidate prefixes and top-10 lists are
  measured separately for every variant/ranker. Overall coverage is the
  unweighted mean of monthly fractions; target macro metrics pool query
  instances across months. Future catalogs never enter a coverage denominator.

### Generated retrieval files

All remain ignored by Git under `outputs/retrieval_v1/`:

- `candidates.parquet`: full-policy ordered candidate rows, keyed by cutoff,
  query/customer, SKU and `candidate_rank`. Includes `selected_source`, four
  overlapping source-membership flags, history/neighbor scores and recent/long
  purchaser counts. Absent source scores are zero. Rank prefixes define K.
- `baseline_recommendations.parquet`: top-10 lists for both rankers, with
  `scope=catalog` or `scope=candidate_200`, cutoff/query/customer/SKU and rank.
  The report also evaluates rankers separately on K=50 and K=100 pools.
- `query_diagnostics.parquet`: every training query, including empty/zero-target
  cases, with primary/repeat/discovery/outside-catalog target sizes and candidate
  count. These target statistics are evaluation data, never retrieval features.
- `training_baseline_report.md` / `.json`: full-policy, standalone, and
  leave-one-source-out results; monthly counts, segment denominators, coverage,
  shortlist histograms, runtime and memory; frozen configuration, package/code
  versions and input/output hashes. JSON contains every monthly/prefix/ablation
  metric, while Markdown presents selected comparison tables.

The run verifies cleaning/snapshot Parquet hashes against their existing reports
and hashes **all** cleaning, snapshot and raw files before/after evaluation.
Generated Parquet files receive complete batched readback and row-count checks.
Runtime includes all four ablations; Linux peak RSS is a cumulative process
high-water mark, not an isolated monthly allocation. Sparse-matrix byte counts
exclude input frames and Python objects. A failed run does not validate reports
left over from an earlier run.

This stage inherits the snapshot coverage/watermark assumption and the existing
cleaning policies. Unresolved codes, including `M` and `S`, remain excluded only
from recommendation V1. Returns do not erase earlier purchases. Source data
does not supply an authoritative product catalog or prove complete ingestion;
these limitations remain unresolved assumptions, not retrieval measurements.

### Verified training retrieval results

The initial frozen-policy run produced **3,864,000 candidates** (200 per query),
**772,800 baseline recommendation rows**, and **19,320 query diagnostics** across
the eleven training cutoffs. The primary macro population is **6,896 queries
(35.69%)**; **12,419 (64.28%)** have no next-month purchases, and **5 (0.026%)**
have only outside-catalog purchases. Repeat/discovery segment macro denominators
are **6,588 / 6,314 queries**, respectively.

| K | Candidate Recall | Repeat Candidate Recall | Discovery Candidate Recall | Oracle NDCG@10 | Oracle Recall@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 50 | 38.05% | 68.62% | 8.53% | 0.7498 | 34.54% |
| 100 | 49.18% | 85.12% | 15.53% | 0.8392 | 40.51% |
| 200 | 60.13% | 95.77% | 27.38% | 0.8985 | 45.11% |

| Scope | Baseline | NDCG@10 | Recall@10 |
| --- | --- | ---: | ---: |
| Candidate 200 | Interleaved order | 0.3185 | 16.81% |
| Candidate 200 | Recent popularity | 0.1422 | 5.54% |
| Candidate 200 | Personalized recency/frequency | 0.3455 | 18.10% |
| Entire known catalog | Recent popularity | 0.1422 | 5.54% |
| Entire known catalog | Personalized recency/frequency | 0.3455 | 18.10% |

In this measured run both unrestricted catalog baselines have the same top-10
metrics as their candidate-200 counterparts. Their recommendations were computed
independently from the entire cutoff catalog. The full report also shows K=50/100
rankings and repeat/discovery ranking recall.

| Source ablation | Candidate Recall@200 |
| --- | ---: |
| Full policy | 60.13% |
| Without history | 47.17% |
| Without neighbors | 59.11% |
| Without recent popularity | 59.10% |
| Without longer popularity | 60.27% |

These comparisons remove a source **and reallocate its scheduling share** under
the fixed merge. They were not used to revise the policy. Mean monthly candidate
coverage at K=50/100/200 is **75.58% / 85.25% / 91.34%**. Top-10 coverage is
**54.83%** for personalized ranking and **0.265%** for recent popularity on
candidate-200 and standalone catalogs. All full and ablation shortlists had 200
items; complete monthly histograms/coverage are in the JSON report.

The run took **208.91 seconds**, including all ablations, with process peak RSS
**1,293.36 MiB** on this environment (Python 3.12.14, NumPy 2.4.6, SciPy 1.15.3).
These resource measurements depend on the machine; timing is not a guarantee.
All three output Parquet files passed complete batched readback. Independent
artifact checks verified every candidate/recommendation's catalog membership,
unique SKUs and contiguous ranks, all training-query retention, metric oracle
ceilings, query-population reconciliation, and matching code/input/output hashes.
Raw, cleaning, and snapshot files remained byte-identical. The initial policy
hash is `684d5b1ba2660fe7f2fe60c1e0da55688c3c059ef436bcf6deb4fa506789aac1`.

All **35 tests passed** with deprecation warnings treated as errors. Recomputing
every training cutoff after dropping all transactions at/after that cutoff
reproduced **all 3,864,000 candidates**, their order, selected sources, membership
flags, and scores exactly. `outputs/retrieval_v1/future_invariance_check.json`
records that additional actual-data verification. Dependency lock validation
and Git whitespace checks also passed. Data and generated outputs are ignored.

**Milestone 1 left validation and test retrieval/ranking unevaluated.** Its
retrieval policy and metric definitions were frozen before Milestone 2 opened
validation. The test split remains unused by Milestone 2.

## Milestone 2: ranking models and temporal validation

With the existing cleaning, snapshots and training candidate outputs present,
run this single command from the repository root:

```bash
UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m scripts.milestone2
```

This builds both feature datasets, reuses all existing training candidates,
generates validation candidates with **unchanged retrieval V1**, trains logistic
regression and the three predefined XGBoost variants, evaluates validation,
saves models/preprocessing, and writes reports/error analysis. No service or
credentials are required. Tracking uses local structured JSON manifests.
The reproducible environment uses `uv sync --locked`; keep `uv.lock` in Git.

```bash
uv run --locked python -W error::DeprecationWarning -m unittest discover -s tests -v
```

Inputs default to `outputs/cleaning/`, `outputs/snapshots/`, and
`outputs/retrieval_v1/`. Outputs default to `outputs/milestone2/`. Override paths
with `--cleaning-dir`, `--snapshot-dir`, `--retrieval-dir`, `--output-dir`,
`--schema`, and `--model-config`. Outputs must be separate from every input
directory. Alternative configurations should have a distinct version and output
directory; this milestone evaluates only the three settings recorded below.

### Past-only features and missing values

The schema contains **19 numeric features**. Timestamps are timezone-naive,
elapsed days are fractional, lower interval boundaries are inclusive and cutoffs
exclusive. First filter paid transactions to `invoice_date < cutoff`, then
apply the existing V1 recommendation eligibility rules. No label table or
future catalog metadata is accepted by the feature-generation function.

| Group | Features | Window / missing behavior |
| --- | --- | --- |
| Customer-item | Previously purchased; distinct invoice count; days since last purchase | Lifetime visible identified personal history; absent count 0, absent recency NaN, prior-purchase flag 0 |
| Customer-item | Quantity purchased | Sum of all eligible canonical lines in `[t-180 days,t)`; absent quantity 0; returns are never netted |
| Item | Distinct identified purchasers, 30 and 180 days | `[t-30/180 days,t)`; anonymous rows do not count; absent count 0 |
| Item | Days since last observed purchase | Lifetime visible eligible catalog evidence, including anonymous purchases; every candidate must already exist in that past catalog |
| Customer | Distinct purchase invoices; distinct SKUs; days since last purchase | All three use `[t-180 days,t)`; frequency counts invoices, not rows |
| Retrieval | Four source flags; history/neighbor scores; recent/long purchaser scores; original position | Copy frozen V1 candidate fields; all source memberships retained, absent scores 0 |

Customer/item IDs, query IDs, cutoff timestamps, labels and weights are metadata
or supervision, **never numeric model features**. Matrix columns follow the
versioned schema's exact order, regardless of DataFrame column order. Stored
features are float32. Baseline evaluation separately retains original float64
history scores so feature rounding cannot alter heuristic ordering.

Both models apply fixed `log1p` to nonnegative skewed counts, quantities,
recencies and candidate position. History/neighbor scores and binary indicators
are not log-transformed. Logistic regression then uses **training-only median
imputation with missing indicators and query-weighted standard scaling**.
XGBoost uses native missing-value branches and no fitted scaling/imputation.
Complete pipelines, schema and configurations are saved together. Customer
features are constant inside a query: they can affect a linear model's score
level, while trees can use them to change item preferences through interactions.

### Examples, weights, models and temporal separation

Every retrieved candidate is labeled 1 if its existing next-month snapshot
target contains that customer–SKU pair, otherwise 0. The candidate rows alone
define training examples; **unretrieved positives are never inserted**. Every
nonempty query has total weight 1 (`1 / candidate_count` per row). In this run,
all queries had exactly 200 candidates and row weights 0.005. Zero-purchase
queries remain all-negative examples. Queries whose positives were all missed
by retrieval also remain all-negative example sets; those differ from genuinely
zero-purchase queries in evaluation diagnostics. No sampling or class balancing
was introduced; peak memory stayed within this environment's available memory.

Before any fit, training query and label target windows must end **at or before
the first validation cutoff**, or the command fails. Verified latest training
target end and first validation cutoff are both **2011-02-01 00:00:00**. Training
uses March 2010–January 2011; validation uses February–April 2011. There are no
random row splits and preprocessing never fits validation rows.

The initial configurations were written before inspecting validation outcomes:

| Model | Configuration |
| --- | --- |
| Logistic | L2, `C=1`, LBFGS, max 400 iterations, tolerance 1e-5 |
| `xgb_shallow` | 200 trees, depth 3, learning rate 0.08, minimum child weight 1, L2 1 |
| `xgb_depth5` | 300 trees, depth 5, learning rate 0.05, minimum child weight 1, L2 5 |
| `xgb_regularized` | 300 trees, depth 4, learning rate 0.05, minimum child weight 2, L2 10 |

XGBoost uses pointwise binary logistic loss, CPU histogram trees, max-bin 128,
full row/column sampling, no positive-class reweighting, and no early stopping.
Seed is 42 and thread count 4. Exactly these three configurations were fit;
none was added in response to results. Scores are logistic `decision_function`
and XGBoost raw margins, **not calibrated purchase probabilities**. Descending
score and ascending normalized SKU give deterministic ranking.

Highest pooled primary-query validation NDCG selects the XGBoost setting; ties
use configuration order. The best learned ranker is compared to the strongest
same-pool heuristic. The predefined advancement screen is +0.01 absolute NDCG,
a positive descriptive confidence-interval lower bound, and at most 0.01 absolute
Recall loss. No validation-dependent retrieval/eligibility/label changes occur.

### Test isolation and unchanged definitions

Shared snapshot Parquet files are read with `split in [train, validation]`.
Paid transactions are read with `invoice_date < 2011-05-01`, so May–July test
transactions are not materialized. Each feature/retrieval cutoff applies its
own stricter past-only filter. Test queries, labels and catalogs are not loaded,
scored, selected on, or evaluated. Legacy report access is limited to integrity,
coverage metadata and train/validation maturity; test outcome summaries are not
used. Whole-file hashing verifies unchanged shared inputs without analyzing
their test partitions.

The existing `ranking_metrics` / `pool_metrics` functions provide the exact V1
definitions. Primary macro metrics include only queries with nonempty known-
catalog targets. Full known-catalog targets define NDCG's ideal denominator and
Recall denominators, including unretrieved positives. Outside-catalog labels
are diagnostics. Zero-target segments remain null/excluded from their segment
means; empty pools score zero on nonempty targets. Every metric reports its
query denominator. Zero-purchase/outside-only queries stay in recommendations
and catalog coverage, and all rankers receive the identical candidate-200 pool.

Coverage@10 is each cutoff's union of recommended SKUs across **all** eligible
queries divided by its entire known catalog, including anonymous evidence.
Overall coverage is the unweighted mean of monthly fractions. Evaluation
segments use past-only snapshot lifetime invoice counts (2–3, 4–9, 10+) and
recency ([0,30], (30,90], (90,180] days), with fixed boundaries.

Paired bootstrap comparisons resample **customers as clusters**, retaining
every primary query of each sampled customer, then calculate a pooled query
mean difference. They use 2,000 replicates, seed 42 and percentile 95% intervals.
The same validation data selected models: these descriptive intervals are
post-selection and are **not proof of future business lift**.

### Saved artifacts

All are ignored by Git under `outputs/milestone2/`:

- `train_features.parquet` / `validation_features.parquet`: ordered numeric
  features, query/customer/SKU/cutoff metadata, split, binary target and row weight.
- `validation_candidates.parquet`: frozen V1 ordered pools with original source
  flags/scores; no test candidates are produced.
- `validation_scores.parquet`: every validation candidate's raw score from all
  four fits, with IDs only as join keys.
- `models/*.joblib`: complete fitted preprocessing/model bundles, schema,
  configuration and training statistics. `*.metadata.json` exposes fitted
  preprocessing, windows/feature order and configuration. XGBoost also saves
  native `*.ubj` models; native files alone require the documented preprocessing.
- `feature_schema.json`, `model_configuration.json`, `retrieval_policy_v1.json`:
  run-local copies of the exact versioned configuration.
- `validation_query_metrics.parquet`: all 7,702 queries, target/pool diagnostics,
  segments, oracle ceilings and each ranker's metrics, including null target cases.
- `validation_recommendations.parquet`: top ten for every heuristic and model,
  with query/cutoff/SKU/ranker/rank; no duplicate query–ranker–SKU instances.
- `validation_comparison_report.md` / `.json`: pooled/monthly/segment comparisons,
  target populations, coverage, retrieval/oracle ceilings, intervals and resources.
- `error_analysis.md` / `.json`: twelve inspected validation queries: three
  improvements, three losses, three retrieval-miss cases and three sparse-history
  cases; top-ten evidence, targets outside retrieval and ranking gaps to oracle.
- `advancement_recommendation.json`: selected XGBoost, best learned ranker,
  strongest heuristic and result of the predefined advancement screen.
- `run_manifest.json`: complete status, configurations, exact read filters,
  source/code/output hashes, dependency versions, row counts, timings/memory,
  unchanged-input checks, full Parquet readbacks and exact model-reload checks.

The complete run verifies all existing raw, cleaning, snapshot and training
retrieval files unchanged. New Parquet files receive complete batched readback.
Saved/reloaded models reproduce **all 1,540,400 validation scores per model
exactly**, using the recorded batch size. A failed run does not validate old
artifacts; check a complete manifest and its matching hashes/configurations.

### Verified validation results and recommendation

Training used **3,864,000 rows** across **19,320 queries**, with **102,599 positive
candidate labels**. Validation has **1,540,400 candidates** for **7,702 queries**.
The primary population is **1,817 queries (23.59%)**, covering **1,214 distinct
customers**; **5,881 queries (76.36%)** have no target purchases, and **4 (0.052%)**
have only outside-catalog purchases. Repeat/discovery macro denominators are
**1,744 / 1,617 queries**.

Candidate Recall@200 is **60.86%**, with repeat/discovery candidate recall
**92.63% / 23.94%**. Oracle NDCG@10 is **0.8933** and oracle Recall@10 **47.55%**.
These ceilings use full known-catalog targets, not retrieved positives alone.

| Ranker | NDCG@10 | Recall@10 | Repeat Recall@10 | Discovery Recall@10 | Mean monthly coverage@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Personalized heuristic | 0.3351 | 18.99% | 32.46% | 0.000% | 56.85% |
| Recent popularity | 0.1279 | 5.69% | 7.80% | 3.577% | 0.24% |
| Interleaved order | 0.3061 | 17.51% | 28.49% | 1.430% | 50.44% |
| Logistic regression | 0.3780 | 21.03% | 37.55% | 0.057% | 34.05% |
| XGBoost shallow | 0.3765 | 21.04% | 37.47% | 0.145% | 35.04% |
| **XGBoost depth 5** | **0.3795** | **21.26%** | **37.95%** | **0.211%** | **36.01%** |
| XGBoost regularized | 0.3775 | 21.26% | 37.94% | 0.139% | 35.85% |

XGBoost depth 5 versus the strongest heuristic has **+0.0444 NDCG**, descriptive
customer-cluster 95% interval **[+0.0380, +0.0505]**, and **+2.27 percentage points
Recall@10**. Logistic's NDCG improvement is **+0.0429**, interval
**[+0.0371, +0.0489]**. All three predefined XGBoost variants are shown above;
there was no search until a winner appeared.

| Validation cutoff | Personalized heuristic NDCG | Logistic NDCG | Selected XGBoost NDCG |
| --- | ---: | ---: | ---: |
| February 2011 | 0.3395 | 0.3761 | 0.3785 |
| March 2011 | 0.3442 | 0.3913 | 0.3911 |
| April 2011 | 0.3208 | 0.3650 | 0.3674 |

The selected XGBoost improves all six predefined frequency/recency segments
versus the heuristic, but logistic is stronger among frequent customers (NDCG
0.4146 vs 0.4075). XGBoost helps sparse histories more (0.2908 vs logistic 0.2689
and heuristic 0.2226). The detailed report includes every monthly and segment
metric and population, rather than hiding these differences.

**Recommend advancing `xgb_depth5` as the production-ranking candidate** under
the predefined selection rule: it has the highest pooled validation NDCG and
passes the meaningful-gain/interval/Recall screen. Its advantage over logistic
is small (**0.00146 NDCG**), so this comparison does not establish a meaningful
tree-versus-linear advantage. Logistic is a credible simpler/faster alternative,
not a failed model. No deployment or test evaluation was performed.

Gains come overwhelmingly from **repeat items**: selected XGBoost adds **5.49
percentage points repeat recall**, versus **0.211 points discovery recall**.
Discovery ranking remains weak despite retrieving some discovery positives.
Recent popularity achieves higher discovery recall (3.577% versus 0.211%);
the selected model's overall advantage is chiefly better repeat ranking.
Coverage falls from 56.85% to 36.01%, indicating a narrower recommendation set
under the measured coverage definition. These tradeoffs remain visible; the
retrieval policy was not changed to compensate.

### Inspected errors and limitations

Of 1,817 primary queries, the selected model improves NDCG on **991**, loses on
**501**, and ties on **325**. **1,545** have at least one relevant known-catalog
SKU missing from retrieval. The twelve detailed cases show:

- Improvements `2011-04-01|16131`, `2011-04-01|14482`, and `2011-03-01|13340`
  recover repeat items with older purchase dates; top-ten repeat hits rise
  2→7, 1→7 and 1→6. These observations support repeat-ranking gains, not a causal
  explanation of any individual feature.
- Losses `2011-02-01|13784` and `2011-02-01|14609` have fully retrieved single-
  item targets. XGBoost moves the correct item from rank 1 to ranks 4 and 3:
  pure ranking losses, with NDCG 1→0.4307 and 1→0.5. `2011-04-01|15903` loses
  repeat hits 6→3 while also missing one target in retrieval.
- Retrieval cases `2011-03-01|14911`, `2011-04-01|14298`, and `2011-03-01|14769`
  miss 239/310, 219/257 and 192/266 known-catalog targets. Ranking cannot recover
  those absent candidates. Their large baskets can still supply ten retrieved
  hits, so oracle NDCG equals 1 even while full-target oracle Recall is low.
- Sparse-history cases `2011-02-01|12352`, `2011-02-01|12458`, and
  `2011-02-01|12582` each have two historical invoices. The model loses one,
  improves two, and still has substantial ranking headroom. For `12582`, all 11
  targets are retrieved but only one reaches top ten: a ranking issue, distinct
  from retrieval misses in the other two cases.

These cases were selected to illustrate failure types, not to estimate average
performance. Retrieved positives outside top ten may reflect capacity as well
as ordering; the oracle comparison separates those effects. No error analysis
caused additional configurations or policy changes.

Total runtime was **188.39 seconds**; cumulative process peak RSS was
**4,282.23 MiB**. Logistic fit took 18.86 s (29 iterations); XGBoost shallow,
depth 5 and regularized fits took 16.79 / 30.17 / 27.27 s. Scoring all 1,540,400
validation rows took **0.35 / 0.68 / 1.89 / 1.27 s**, respectively, in batches of
100,000 including ordered extraction/preprocessing. These are environment-
specific batch measurements, not production latency guarantees.

Existing source-coverage and unresolved-code assumptions remain unchanged.
Nonpurchase labels describe observed purchase absence, not certified dislike;
there is no recommendation-exposure or propensity data. Primary metrics
condition on future purchasers and do not quantify utility for zero-purchase
customers, even though those queries remain in training/outputs. The selected
model and its interval reuse validation, and temporal generalization beyond it
has not been measured. The **test split remains unopened and unevaluated**.
Milestone 2 stops here: no APIs, deployment or monitoring.

All **50 tests passed** with deprecation warnings treated as errors. Additional
actual-data checks verified every training/validation row's original candidate
membership/order/fields, exact target labels, equal query weights, all-zero
examples for zero-purchase queries, and every recommendation's pool/catalog
membership and oracle ceiling. Actual logistic imputation/scaling statistics
matched training data only. All original V1 source-code hashes still match its
training report. Rebuilding all three validation cutoffs after deleting every
transaction at/after each cutoff from the **in-memory verification input**
reproduced all **1,540,400 numeric feature rows exactly**, including NaN
locations; the unchanged source files were not edited. The supplementary
`feature_invariance_check.json` records this check. Saved model scores, raw/
cleaning/snapshot/retrieval inputs and model/config/code artifacts passed hash
or exact-array verification. The frozen V1 policy SHA remains
`684d5b1ba2660fe7f2fe60c1e0da55688c3c059ef436bcf6deb4fa506789aac1`.

## Milestone 3: historical release and local inference

Milestone 3 resolves model selection without another fit or tuning round.
Cleaning, recommendation eligibility, retrieval V1, the ordered 19-feature
schema, preprocessing learned from training, and label/metric definitions
remain unchanged. The previously selected depth-5 model is compared directly
with the saved logistic model on the same validation queries and pools.

| Saved model | NDCG@10 | Recall@10 | Repeat recall@10 | Discovery recall@10 | Model artifact | Warm 200-row scoring + top10 p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Logistic | 0.378038 | 0.210269 | 0.375491 | 0.000573 | 2,951 bytes | 0.520 / 0.636 ms |
| xgb_depth5 | 0.379499 | 0.212597 | 0.379527 | 0.002113 | 195,399 bytes | 0.732 / 1.074 ms |

The XGBoost-minus-logistic NDCG difference is **+0.001461**, with a descriptive
95% paired customer-cluster bootstrap interval **[-0.002831, +0.005780]**
(2,000 seeded replicates; 1,817 primary queries from 1,214 customers). The
interval includes zero but also permits a modest XGBoost advantage; this does
**not** establish equivalence. Recall differs by +0.002328, repeat recall by
+0.004037, and discovery recall by +0.001540. Both models remain weak at
ranking discovery products, and retrieval's discovery limitations are unchanged.

**Release V1 uses logistic regression.** The observed NDCG difference is below
the predefined 0.005 practical threshold, its paired interval includes zero,
and logistic sacrifices less than 0.005 Recall@10 while using a model artifact
about 66 times smaller. Its warm artifact load p50 is 0.398 ms versus 5.297 ms,
and its warm scoring is faster. This is a practical preference for the simpler
model, not proof of equal quality or future business lift. No additional model
configurations were added. The M2 report remains a record of the earlier
quality-only selection; M3 resolves the serving choice using these measurements.

Timing used 500 seeded, distinct February validation queries of exactly 200
candidates, 30 warmup queries, four native threads, one caller, and fitted
preprocessing plus lexical-tie top10 sorting. Feature matrices were extracted
once, as they are at API startup. The existing dataframe scoring helper, which
also extracts features and configures threads per call, had p95 5.835 ms for
logistic and 7.030 ms for XGBoost. Both paths produced matching scores on all
500 queries. Fresh-Python import-plus-load p50 was 1,081 ms for logistic and
947 ms for XGBoost over three subprocesses: dependency imports dominate these
noisy cold measurements, so there is no claimed cold-start advantage. Full
conditions and results are in `outputs/milestone3/model_comparison.json` and
`.md`. Scores remain ranking margins, not calibrated purchase probabilities.

### Build and start

Run from the repository root, after the existing M2 outputs have been built:

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked
# One command builds the immutable bundle. It compares saved models if no
# comparison report exists; subsequent builds reuse and verify that evidence.
uv run --locked python -m scripts.build_release
# One command starts local inference using outputs/releases/latest.json.
uv run --locked python -m scripts.local_api
```

The server binds `127.0.0.1:8000` by default. An explicit immutable release can
be selected with `--bundle outputs/releases/<release_id>` or `BUNDLE_PATH`.
There is one loaded release per process, one worker, and no per-request history
lookup, retrieval generation, model training, or mutable customer state.

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/model
curl 'http://127.0.0.1:8000/recommendations/12347?k=10'
curl 'http://127.0.0.1:8000/recommendations/unknown-customer?k=10'
```

- `/health` reports process liveness, even if the bundle is invalid.
- `/ready` succeeds only after integrity, compatibility, schema, index,
  catalog and model checks plus one warm scoring call. Invalid bundles keep
  liveness but return HTTP 503 from readiness, metadata and recommendations.
- `/model` returns release metadata, historical `data_as_of`, model training
  and validation windows, dependency/policy versions and evaluation summary.
- `/recommendations/{customer_id}` scores up to 200 stored candidates and
  returns at most `k` items (default 10, integer range 1–20). Ties sort by
  lexical SKU. Normalized customer IDs are exact string lookup keys; IDs are
  never model inputs. Unknown or ineligible customers share an explicitly
  identified `popularity_fallback` mode. Fallback scores are distinct identified
  30-day purchaser counts, whereas personalized scores are raw model margins.
  Response fields identify the score kind, mode, model/release version and
  historical date, with `scores_are_calibrated_probabilities: false`.

Only `k` is accepted as a query parameter. Historical-date overrides and an
`/events` endpoint are not implemented. Item descriptions come from the latest
nonempty, eligible paid transaction strictly before the serving cutoff; equal
transaction times use record ID as a deterministic tie break. Descriptions are
historical observations, not live product names, availability or prices.

### Bundle contents and provenance

The historical serving cutoff is **2011-05-01 00:00:00**, exclusive, in the
workbook's timezone-naive event-time convention. It allows the already observed
February–April validation labels to finish by May 1. The model's last training
prediction cutoff is **January 1, 2011**, and its final training label window
ends **February 1, 2011**. These dates are different from serving `data_as_of`.
The bundle refreshes history/candidates only; it does not refit on validation.

The builder applies a Parquet predicate `invoice_date < 2011-05-01` before
loading paid rows and reuses the existing snapshot builder with a serving
watermark equal to the cutoff. It produces **2,443 eligible customer slices,
488,600 candidate feature rows and 4,216 historical catalog items**. The
next-month labels are deliberately immature and absent. This serving date is
also the first scheduled test prediction date, but **no test snapshot rows,
test labels, test-era purchases or test quality metrics are opened**. It is
prospective historical inference constructed from pre-May history only.

`outputs/releases/release_v1_<sha256>/` contains:

- `model.joblib` and `model.metadata.json`: the selected existing fitted model
  and training-only preprocessing, copied without another fit.
- `candidate_features.parquet` and `customer_index.json`: metadata plus the
  exact ordered features, grouped into validated contiguous customer slices.
  No labels or training weights enter the serving feature file.
- `items.parquet`: the past-only eligible catalog and display descriptions.
- `fallback.parquet`: up to 20 known-catalog SKUs ordered by distinct identified
  purchasers in `[data_as_of - 30 days, data_as_of)`, then lexical SKU.
  Anonymous purchases contribute catalog evidence only. Zero-count catalog
  items backfill only if fewer than 20 positive-count items exist.
- `policies/`: unchanged cleaning stock-code, recommendation/retrieval,
  feature/model configurations, plus the release serving/selection configuration.
- `model_comparison.json`: fixed validation comparison and operational evidence.
- `code/`: exact Python sources, dependency lock and image configuration.
- `manifest.json`: content-derived release/model IDs, serving and training/label
  windows, HEAD commit, dirty-worktree indicator, exact payload/source/config
  hashes, input data/report hashes, dependency versions and evaluation summary.

The current HEAD reference predates the uncommitted milestone implementation;
the manifest explicitly says so. The bundled source hashes identify the code
actually used; HEAD alone would not. Source-file hashes identify the complete,
unchanged input artifacts for audit provenance; only filtered pre-cutoff data
appears in serving payloads. Snapshot eligibility, anonymous-customer scope and
unresolved-stock-code assumptions remain as documented in earlier milestones.

Bundles are written to a staging directory, validated, made read-only
(directories 0555, files 0444), and published under their content hash. Existing
releases are never rewritten; identical inputs/evidence/source versions resolve
to the same release. The small `latest.json` pointer is outside the immutable
release. A newly measured comparison report or changed code/configuration gives
a new release identity. All bundles, models, downloaded data and generated
reports remain ignored by Git under `/outputs/` and `/data/`.

The loader verifies manifest and all payload hashes **before loading the local
model artifact**, requires the ordered schema and frozen policies to match,
and checks dependency and bundled-source versions against the running code.
It validates customer slices, duplicate SKUs, serving limits, metadata dates,
catalog membership and fallback ordering. Editing the current source or lock
requires rebuilding both bundle and image; an old bundle fails readiness
against changed serving code. The deployment mount is read-only as well.

### Docker and local verification

The image pins Python 3.12.14 by image digest, installs uv 0.12.19, and installs
Python dependencies from `uv.lock` using `uv sync --locked --no-dev`. It runs
as UID/GID 10001, includes code/config only, and mounts the generated bundle
separately. The `/ready` endpoint is its Docker health check.

```bash
mkdir -p .cache/docker
DOCKER_CONFIG="$PWD/.cache/docker" docker build -t repeat-order-recommender:milestone3 .
# Replace <release_id> with the ID printed by build_release (also in latest.json).
docker run --rm -p 127.0.0.1:8000:8000 \
  --mount type=bind,source="$PWD/outputs/releases/<release_id>",target=/bundle,readonly \
  repeat-order-recommender:milestone3
```

In this managed environment, Docker build containers needed the environment's
existing proxy, a DNS mapping for its hostname, and its CA certificate as a
BuildKit secret. This maintained certificate verification; the proxy/certificate
is not stored in the image. The verified environment-specific build command is:

```bash
python - <<'PY'
import os, socket, subprocess
from urllib.parse import urlparse
proxy_host = urlparse(os.environ['HTTPS_PROXY']).hostname
proxy_ip = socket.gethostbyname(proxy_host)
env = os.environ.copy()
env['DOCKER_CONFIG'] = os.getcwd() + '/.cache/docker'
subprocess.run([
    'docker', 'build', '--network=host',
    '--add-host', proxy_host + ':' + proxy_ip,
    '--build-arg', 'HTTP_PROXY', '--build-arg', 'HTTPS_PROXY', '--build-arg', 'NO_PROXY',
    '--secret', 'id=build_ca,src=' + os.environ['CODEX_PROXY_CERT'],
    '-t', 'repeat-order-recommender:milestone3', '.'
], env=env, check=True)
PY
```

Reproduce the offline tests and the real-process loopback load measurements:

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m scripts.load_test
```

`load_test` starts/stops one actual uvicorn subprocess, excludes 50 warmup
requests per level, and runs closed-loop clients for 20 seconds at each of
1 and 5 concurrent clients. A shared request sequence keeps the configured
90% personalized / 10% unknown-fallback schedule; the report records actual
mode counts. It reports every HTTP/transport/content error, successful and
all-attempt latencies, throughput, process-to-ready time, bundle-load time,
and API-process RSS/high-water RSS. HTTP access logs are disabled. Structured
request logs use route templates and contain latency, status, mode and release
version without raw customer IDs. The local log file is ignored by Git.

New files are intentionally separate from the frozen pipeline:

- `scripts/model_selection.py` compares two saved models, clusters the existing
  validation query metrics by customer, benchmarks inference and records choice.
- `config/release_v1.json` versions the serving cutoff, limits, practical model
  selection thresholds, measurement settings and 250 ms engineering target.
- `scripts/build_release.py` derives serving state from pre-cutoff paid rows
  and verifies upstream M2 provenance before publishing an immutable bundle.
- `scripts/release_bundle.py` writes, validates, loads and scores that bundle.
- `scripts/local_api.py` supplies the four endpoints and private structured logs.
- `scripts/load_test.py` measures an actual local API subprocess and writes
  `outputs/milestone3/load_test_report.json` / `.md` and `api_requests.jsonl`.
- `Dockerfile` / `.dockerignore` lock image inputs and exclude data/outputs.
- `tests/test_release_bundle.py`, `test_release_build.py` and `test_load_test.py`
  cover corruption/version/schema mismatches, invalid readiness, known/unknown
  responses, K validation, exact-score persistence, deterministic ties, no
  duplicate recommendations, API/offline equivalence, past-only display data,
  release permissions and correct load-report/traffic-share arithmetic.

This is a historical local demonstrator. Unknown/ineligible-customer fallback
has no personalized quality estimate, and validation selection is retrospective.
The no-purchase primary-metric exclusion, discovery limitations, observed-data
coverage assumptions and unresolved-code policy remain unchanged. No cloud
service, production rollout, event ingestion, hosted tracking or monitoring
infrastructure is set up. The test split remains unevaluated.

### Recorded local verification results

The final measured run used Python 3.12.14 on an **AMD EPYC 9V74**, with five
visible/affinity CPUs, a **four-CPU cgroup quota**, and a 32 GiB memory limit.
One API process and the client process shared that quota; HTTP used loopback
keepalive and the above 90/10 workload. These are measurements of this local
setup, not cloud capacity or latency guarantees.

| Concurrent clients | Completed requests | Warm p50 | Warm p95 | Requests/sec | Error rate |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 9,171 | 2.072 ms | 2.909 ms | 458.5 | 0% |
| 5 | 12,512 | 7.727 ms | 12.526 ms | 625.4 | 0% |

Process-to-ready startup was **1,539 ms**, including **400 ms** for bundle
validation/loading. Peak API-process RSS was **472.7 MiB**; steady RSS was
424.3 / 427.9 MiB after the one-/five-client runs. The five-client warm p95
met the **250 ms engineering target**. The report records actual fallback
shares (9.999% / 9.998%), both successful/all-attempt latency distributions,
measurement duration and status counts. `load_test_report.json` is the full
record; reruns produce new timing measurements without altering a bundle.

The built `repeat-order-recommender:milestone3` image was exercised as UID
10001 with a read-only release mount. It passed readiness, metadata and K
validation; ten actual known-customer rankings and unknown-customer fallback
matched the offline bundle responses exactly. Image ID, size and checks are
recorded in `outputs/milestone3/docker_verification.json`. Model artifact sizes
above are distinct from the complete image size (approximately 1.03 GB), which
retains the frozen project's shared Python/ML dependencies. Input features and
libraries dominate serving memory even though the logistic artifact is small.

All **61 standard unittest tests passed**. A separate, optional
`-Werror::DeprecationWarning` import check fails on Starlette's reference to the
now-deprecated AnyIO `BlockingPortal` alias before application tests execute.
Ordinary test execution and the actual API/Docker checks pass with the locked
versions; strict third-party deprecation-free execution is not claimed.
Raw, cleaning, snapshot, retrieval and all M2 artifact/source/config hashes
remain unchanged. There were no additional fits or test quality evaluations.
