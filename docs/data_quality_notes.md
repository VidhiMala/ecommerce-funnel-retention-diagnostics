# Data Quality Notes: Raw Profile (Day 1)

**Evidence:** `reports/day01_raw_profile.txt` (4 files, 16,536,158 rows). Labels as in `docs/data_dictionary.md`:
- **[O]** observed in the report
- **[D]** derived by arithmetic from [O] values
- **[A]** assumption, to be verified

**Status (updated 2026-11-18):** measured exactly in the Day 2 audit (`reports/day02_audit_report.md`). Resolutions approved in `docs/cleaning_rules.md` (C1–C12); results in `reports/day03_cleaning_log.md`. Original Day 1 status, kept below for the record: issues **identified only**. No cleaning has been applied and the raw CSVs are unchanged. Every "Proposed handling" below is a proposal for Day 2, decided only after the Day 2 audit measures each issue exactly.

**Severity:**
- **High:** could distort headline metrics if ignored.
- **Medium:** limits a specific analysis.
- **Low:** cosmetic, or a documentation matter.

---

## Summary

| ID | Issue | Severity | Measured so far? |
|---|---|---|---|
| DQ-01 | `category_code` almost entirely missing | Medium | ✅ null % |
| DQ-02 | `brand` missing for about 41–43% of rows | Medium | ✅ null % |
| DQ-03 | Negative prices (min −79.37) | **High** | ⚠️ min only; count unknown |
| DQ-04 | Missing `user_session` | Low | ✅ null % |
| DQ-05 | "0.00% null" is rounded, not proven zero | Low | ❌ exact counts needed |
| DQ-06 | Duplicate events not yet measured | **High** | ❌ |
| DQ-07 | Event counts don't form a funnel shape | **High** | ✅ counts |
| DQ-08 | No `order_id` or `quantity` column | **High** | ✅ schema |
| DQ-09 | Time zone: UTC stored, local time unknown | Medium | ✅ raw text |
| DQ-10 | December volume lower than other months | Medium | ✅ counts; cause unknown |
| DQ-11 | Zero prices and high-price outliers not yet checked | Medium | ❌ |
| DQ-12 | Approximate distinct counts; months can't be summed | Low | ✅ method |
| DQ-13 | Typo in source category label | Low | ✅ |
| DQ-14 | Files sort alphabetically, not by month | Low | ✅ |

---

## DQ-01: `category_code` almost entirely missing (Medium)
- **Evidence** [O]:
  - null % is 98.36 (Oct), 98.37 (Nov), 98.35 (Dec), 98.25 (Jan)
  - only 10–12 distinct values per month
- **In contrast** [O]: `category_id` is 0.00% null, with ≈475–485 distinct values per month.
- **Impact:** a category analysis built on `category_code` would describe only about 1.6–1.8% of events [D].
- **Proposed handling:**
  - Use `category_id` as the category dimension.
  - Keep `category_code` as an optional readable label.
  - Never drop rows because `category_code` is null.

## DQ-02: Missing `brand` (Medium)
- **Evidence** [O]: null % is 40.45 (Oct), 42.84 (Nov), 42.74 (Dec), 41.64 (Jan).
- **Impact:** brand rankings cover only about 57–60% of events [D]. They would be biased if missing brands are concentrated in certain categories or price bands [A].
- **Day 2 checks:**
  - null-brand share by `event_type`, `category_id` and price band
  - whether a given `product_id` is sometimes branded and sometimes not, which would allow back-filling from the product's other rows
- **Proposed handling:**
  - Keep all rows.
  - Label missing values `'unknown'` in reporting tables.
  - State coverage alongside every brand-level result.

## DQ-03: Negative prices (High)
- **Evidence** [O]: minimum `price` is **−79.37 in every month**. That identical minimum across all four months suggests a specific product or system entry rather than random noise [A].
- **Not yet known:**
  - how many rows have a negative price
  - which event types and products they belong to
  - whether they appear on `purchase` events, where they would reduce revenue directly
- **Day 2 checks:** count and value of rows with `price < 0`, broken down by `event_type`, `product_id` and month.
- **Proposed handling:**
  - Exclude prices ≤ 0 from revenue and price-band metrics.
  - Keep those rows flagged (not deleted) for event counts, if they're otherwise valid.
  - Record the decision and the row count in the cleaning log.

## DQ-04: Missing `user_session` (Low)
- **Evidence** [O]: null % is 0.02 (Oct), 0.02 (Nov), 0.02 (Dec), 0.03 (Jan).
- **Impact:** these rows can't be placed in a session, so session funnels and abandonment ignore them. They're still valid for user-level metrics.
- **Proposed handling:**
  - Exclude them from session-level analyses only.
  - Report the exact count from the Day 2 audit.

## DQ-05: "0.00% null" is rounded (Low)
- **Evidence** [O]: `null_percentage` is shown to two decimals. 0.00 means under 0.005% of rows, which could still be up to roughly 180–230 rows per month, depending on the month's size [D].
- **Columns affected:** `event_time`, `event_type`, `product_id`, `category_id`, `price`, `user_id`.
- **Proposed handling:**
  - Measure exact null counts on Day 2.
  - Turn "no nulls" into a pytest test on Day 3.

## DQ-06: Duplicate events not yet measured (High)
- **Evidence:** the profiler didn't test for duplicates.
  - [O] In the Oct sample, user `463240011` added several different products to cart within seconds in the same session. That pattern is consistent with normal behaviour, so it's not evidence of duplicates.
  - [D] Many events share the same second, so a duplicate check must compare every column, not the timestamp alone.
- **Impact:** duplicate `purchase` rows would inflate revenue and orders. Duplicate `cart` rows would inflate the abandonment figures.
- **Day 2 checks:**
  - exact duplicate rows (all 9 columns)
  - "near duplicates": same user, session, product, event type and second
- **Proposed handling:** drop exact duplicates, and document the count and the rule.

## DQ-07: Event counts don't form a funnel shape (High)
- **Evidence** [D], from counts in [O]:

  | Month | cart ÷ view | remove_from_cart ÷ cart | purchase ÷ cart |
  |---|---|---|---|
  | Oct | 0.662 | 0.618 | 0.199 |
  | Nov | 0.632 | 0.706 | 0.246 |
  | Dec | 0.536 | 0.717 | 0.230 |
  | Jan | 0.564 | 0.710 | 0.230 |
  | 4 months | 0.600 | 0.686 | 0.226 |

  Cart events equal 54–66% of view events. That's far higher than typical view-to-cart rates in e-commerce [A]. Possible explanations [A]:
  - views aren't logged on every page that lets you add to cart
  - quantity changes are logged as repeated cart events
  - carts are re-added after removal
- **Impact:** **dividing event counts gives misleading conversion rates.**
- **Proposed handling:**
  - Build funnels on whether a session or user **ever** reached each stage, not on raw event counts.
  - On Day 2, measure how many sessions have a cart but no view.
  - Write the funnel definition into `docs/metric_definitions.md`.

## DQ-08: No `order_id` or `quantity` column (High)
- **Evidence** [O]: the schema has 9 columns and neither is present.
- **Impact:**
  - Order count and average order value need a stated proxy.
  - Revenue assumes one unit per purchase row [A].
  - Repeated purchase rows for the same product in one session are ambiguous: quantity or duplicates? (See DQ-06.)
- **Proposed handling:**
  - Order = a session with ≥1 purchase.
  - Revenue = sum of `price` over valid purchase rows.
  - State both definitions in the metric definitions and the report.

## DQ-09: Time zone (Medium)
- **Evidence** [O]:
  - The raw text ends in ` UTC`.
  - DuckDB stores it as a time-zone-free TIMESTAMP holding the UTC clock time.
  - The store's local time zone isn't in the data.
- **Impact:**
  - hour-of-day and weekday patterns
  - which day or month an event near midnight belongs to
- **Proposed handling:**
  - Keep UTC everywhere, and label time-of-day charts "UTC".
  - Convert only if a reliable source for the store's time zone is found. Converting without one would be an unsourced assumption.

## DQ-10: December volume is lower (Medium)
- **Evidence** [O]: Dec has 3,533,286 rows, against 4,102,283 (Oct), 4,635,837 (Nov) and 4,264,752 (Jan).
  - [D] That's about 14% below Oct and 24% below Nov.
  - [O] Dec timestamps still cover the whole month: 2019-12-01 00:00:00 to 2019-12-31 23:59:57.
- **Not yet known:** whether this is real seasonality or a logging gap, such as missing days or hours inside December.
- **Day 2 check:** daily event counts for all months, to spot missing days or abrupt drops.
- **Proposed handling:**
  - If a gap is found, flag the affected dates and note the effect on monthly comparisons and cohorts.
  - Never back-fill made-up values.

## DQ-11: Zero prices and high-price outliers not yet checked (Medium)
- **Evidence** [O]: the report shows only min and max (max 307.6 in Oct, 327.78 in Nov–Jan). Zero prices and the price distribution weren't profiled.
- **Day 2 checks:**
  - count of `price = 0`
  - price percentiles (p1, p50, p99)
  - whether the same product has very different prices across events, which also feeds upgrade 1B
- **Proposed handling:** exclude prices ≤ 0 (see DQ-03). Keep high prices unless they're clearly invalid; a high price is not an error in itself.

## DQ-12: Approximate distinct counts (Low)
- **Evidence** [O]: distinct counts come from `approx_unique` (a HyperLogLog estimate).
  - [A] Users and products repeat across months, so monthly figures can't be added together.
- **Proposed handling:** compute exact 4-month distinct users, sessions and products on Day 2, and use only those exact figures in KPIs.

## DQ-13: Typo in a source category label (Low)
- **Evidence** [O]: `stationery.cartrige` appears as a `category_code` value.
- **Proposed handling:** keep the source value unchanged. Optionally correct it in a display label in Power BI, and document the change.

## DQ-14: Files sort alphabetically, not by month (Low)
- **Evidence** [O]: the profiler processed the files as Dec, Nov, Oct, Jan, which is alphabetical order by file name.
- **Impact:** any pipeline logic that relies on file order would put the months in the wrong order.
- **Proposed handling:** always order data by `event_time`; never by file name.

---

## Checks that passed [O]/[D]
- All four files share an identical schema: same 9 columns, same order, same types.
- `event_type` has exactly the four expected values in every month; there are no unexpected labels.
- Each month's timestamps fall inside that calendar month.
- Each month's event-type counts sum exactly to its row count.
- Total rows = 16,536,158, matching the four monthly counts.
- The `remove_from_cart` event exists in every month, so upgrade 1A is feasible.

## Day 2 audit checklist (from the issues above)
- [ ] Exact null counts for all 9 columns (DQ-05, DQ-04)
- [ ] Negative and zero prices: count, value, by event type and product (DQ-03, DQ-11)
- [ ] Price percentiles and per-product price variation (DQ-11; upgrade 1B feasibility)
- [ ] Exact and near-duplicate rows (DQ-06)
- [ ] Sessions with a cart but no view; purchases without a cart (DQ-07)
- [ ] Daily event counts, to check for gaps (DQ-10)
- [ ] Brand missingness by event type, category and price band; back-fill check (DQ-02)
- [ ] Exact 4-month distinct users, sessions and products (DQ-12)
- [ ] Does any session span more than one month or file?
