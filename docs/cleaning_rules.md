# Cleaning Rules (approved 2026-11-18)

**Status:** approved by the project owner on 2026-11-18 (C1–C12, A1, A2).
**Implemented in:** `src/clean.py`.
**Results:** `reports/day03_cleaning_log.md`.
**Evidence:** every figure below comes from the Day 2 audit (`reports/day02_audit_report.md`, `reports/day02_audit/*.csv`). Figures marked **[D]** are simple arithmetic on audit figures.

## Principles
1. **Raw CSVs are never modified.** Cleaning reads the staged typed copy and writes new files to `data/processed/events_clean/`, one folder per month.
2. **Nothing is silently discarded.** The only structural change is merging exact duplicates into one row with an `event_copies` count. That loses nothing, because all columns are identical. `SUM(event_copies)` must equal the 16,536,158 raw rows.
3. **Flag, don't delete.** Every other issue gets a flag column. The original values stay alongside the flags.
4. **Output is accepted only after it passes validation.** `src/clean.py` writes to a temporary folder, validates what it wrote against the raw data, and only then replaces the previous output. On any failure it stops, keeps the previous output, and reports the failure.

## Rules

| ID | Issue | Rule | Output columns | Evidence (audit) |
|---|---|---|---|---|
| **C1** | Exact duplicate rows (all 9 raw columns identical, NULLs treated as equal) | Merge each group into **one row** with `event_copies` = number of identical raw rows. Raw counts = `SUM(event_copies)`; deduplicated counts = `COUNT(*)`. | `event_copies`, `is_duplicate_merged` | 868,808 extra rows in 826,259 groups: remove_from_cart 782,059 · cart 85,486 · purchase 793 · view 470 |
| **C2** | Near-duplicates | No action | — | 0 groups for every event type |
| **C3** | Same product bought at 2+ different timestamps in one session | Keep. These are separate purchase rows, each counted as one unit. Their value is reported in the cleaning log. | — | 6,795 of 1,037,088 session–product pairs; 7,133 extra rows |
| **C4** | Prices ≤ 0 | Keep and flag. Excluded from revenue, price bands, average order value, cart-leakage values and price-change analysis. Count and value always reported separately. | `is_valid_price` (= `price > 0`) | 94 negative rows (5 products that never have a positive price); 32,903 zero rows; 92 purchase rows worth −2,746.05 |
| **C5** | Timestamp problems | No action. Parsed UTC timestamp stored as `event_time`; a permanent test asserts no nulls and no events outside their month. | `event_time` (UTC), `event_month` | 0 nulls, 0 wrong formats, 0 outside their month |
| **C6** | Missing `user_session` | Keep and flag. Excluded from session-level metrics only. | `has_session` | 3,543 rows (cart 2,882 · remove 636 · view 25 · purchase 0) |
| **C7** | Session integrity | Session key = `user_id` + `user_session`. Platform session IDs are kept; sessions are **not** re-split by inactivity. Sessions over 24 h and session IDs shared by 2+ users are flagged. Month rules are in `docs/metric_definitions.md`. | `session_key`, `session_id_shared`, `session_over_24h` | 191 sessions with 2+ users; 22,791 sessions over 24 h; 4,762 span 2+ months |
| **C8** | Missing brand | Fill **only** when the product has exactly one known brand elsewhere. Original value kept. "unknown" is a label in exported tables only. | `brand_raw`, `brand`, `brand_filled` | 54,537 fillable raw rows; 23 products with 2+ brands are not filled; 22,114 products never have a brand |
| **C9** | Category | Keep each event's own `category_id`. Add each product's primary category: the `category_id` it appears under most often across raw rows, ties broken by the lowest ID. Flag products with 2+ categories. `category_code` can't be filled (0 fillable rows). | `primary_category_id`, `multi_category_product` | 1,614 products in 2+ category IDs; 0 IDs with 2+ codes; 0 partly missing |
| **C10** | Low-volume days | No cleaning. Documented as a limitation: Dec 26–Jan 1 trough, lowest day 2019-12-31 with 31,096 events. | — | 0 zero-event hours; 1 day below half its month's median |
| **C11** | Funnel anomalies | No cleaning. Funnel analysis uses stage flags per **session** and per **user**, never a product-level funnel. The cart-abandonment time window is **not yet decided**: options are in `docs/metric_definitions.md` for review. | — | 73.8% [D] of carted session–product pairs have no view; 50.6% [D] of purchased pairs have no cart |
| **C12** | High prices | Keep everything; no outlier removal | — | Max 327.78; all-rows p99.9 219.05 |

## Approved metric amendments

| ID | Amendment |
|---|---|
| **A1** | **Estimated purchase revenue** counts each merged purchase event **once**, ignoring `event_copies`. The 793 duplicate purchase rows and their value are reported separately. `event_copies` is kept for traceability. |
| **A2** | **Purchasing sessions:** the number of distinct `session_key` values (`user_id` + `user_session`) with at least one **valid** purchase (`price > 0`). This approximates orders and is **not an exact order count**, because the dataset has no order ID. |

## Validations `src/clean.py` must pass (any failure stops the run)
- `SUM(event_copies)` = raw rows, in total, per event type and per month.
- Output rows = raw rows minus exact-duplicate extra rows, counted independently from the raw data.
- Every raw row is preserved: a fingerprint of all rows, weighted by `event_copies`, equals the fingerprint of the raw rows.
- Revenue is consistent:
  - raw revenue = `SUM(price × event_copies)` over valid clean purchases
  - A1 revenue = raw revenue − value of the duplicate purchase rows
- Flags match independently computed raw counts:
  - null brand
  - filled brand
  - null session
  - products in 2+ categories
  - non-positive prices
- `brand` = `brand_raw` wherever `brand_raw` is present.
- The output schema is as expected, with no nulls in required columns.
- Purchasing sessions by month of first valid purchase add up to the A2 total. Added 2026-11-24 after a Day 4 reconciliation check found that rows without a session could form a NULL "session" in the monthly table. That couldn't happen in the real data (no purchase lacks a session), but the query is fixed and tested.
