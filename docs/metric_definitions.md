# Metric Definitions

Single source of truth for every metric in the SQL, Python, Excel and Power BI layers.
Base table: `data/processed/events_clean/`, as described in `docs/cleaning_rules.md`.

**Status labels:**
- ✅ **Approved:** decided by the project owner (date given).
- 🟡 **Proposed:** a default to confirm when the related analysis day starts. Not final.
- ⏳ **Pending:** options listed below; **no decision yet**.

---

## 1. Approved definitions (2026-11-16, amended 2026-11-18)

### 1.1 Time zone ✅
All timestamps stay in UTC (the raw data carries a `UTC` suffix). Every time-based chart and table is labelled "UTC". No local-time conversion.

### 1.2 Estimated purchase revenue ✅ (A1)
Sum of `price` over `purchase` events where `price > 0`, assuming one unit per purchase row (no `quantity` column).
- Rows with `price ≤ 0` are excluded from this metric but **kept and flagged** (`is_valid_price = false`). The excluded record count and their summed value are always reported separately.
- Merged duplicate purchase events (C1) are counted **once**. The duplicate purchase rows and their value are reported separately. `event_copies` is kept for traceability.
- **Duplicate purchase copies (real data, Day 3): 793 in total.** 789 have a valid price and are worth 3,194.81; A1 revenue leaves them out, and they're reported as `dup_purchase_copies_excluded` / `dup_purchase_value_excluded`. The other **4** have a price ≤ 0; they're already inside the 92 non-positive purchase rows (`nonpositive_purchase_rows`). The overview shows the total as `dup_purchase_copies_all` (approved R3, 2026-11-24).
- SQL: `SUM(price) FILTER (WHERE event_type = 'purchase' AND is_valid_price)` on the clean table.
- Currency: as in the source data. No FX conversion.

### 1.3 Purchasing sessions ✅ (A2)
Number of distinct `session_key` values (`user_id` + `user_session`) containing **at least one valid purchase** (`price > 0`).
- This is a **proxy for orders, not an exact order count**: the dataset has no order ID. Every table and chart using it says so.
- Month: the month (UTC) of the session's **first valid purchase**.

---

## 2. Approved data conventions (cleaning rules, 2026-11-18)

| Convention | Definition |
|---|---|
| Raw event count | `SUM(event_copies)`: equals the original CSV rows (16,536,158 in total) |
| Deduplicated event count | `COUNT(*)` on the clean table: identical raw rows counted once (C1) |
| Valid price | `is_valid_price` (= `price > 0`). Non-positive prices are excluded from revenue, price bands, average order value, cart-leakage values and price-change analysis (C4). |
| Session | `session_key` = `user_id:user_session`. Rows without a session (`has_session = false`) are excluded from session metrics only (C6, C7). Sessions are not re-split by inactivity. |
| Event month | The month (UTC) of the event itself (C7) |
| Brand | `brand`, after single-brand fill (C8). Missing values are shown as "unknown" in exported tables. Every brand-level result states its coverage. |
| Category | `category_id` for event-level analysis; `primary_category_id` for product-level analysis (C9). Categories are identified by ID; `category_code` covers too few rows to use. |
| Funnel approach | Stage flags per **session** and per **user**. **No product-level funnel** (C11). |

---

## 3. Business performance metrics ✅ (approved 2026-11-24, decisions D1–D4)

| Metric | Definition | SQL |
|---|---|---|
| Visitors | Distinct `user_id` with ≥ 1 event in the period | `01`, `02` |
| Buyers | Distinct `user_id` with ≥ 1 valid purchase in the period | `01`, `02` |
| Average order value (proxy) | Estimated purchase revenue ÷ purchasing sessions. Always labelled a proxy. | `01`, `02` |
| Revenue per visitor | Estimated purchase revenue ÷ visitors | `01`, `02` |
| Session conversion | Purchasing sessions ÷ sessions (sessions with a `session_key`). **D1:** period total on Day 4; monthly trend on Day 5. | `01` |
| User conversion | Buyers ÷ visitors, over a stated period. **D1:** as above. | `01` |
| Activity counts (D2) | Deduplicated events `COUNT(*)` by default; raw events `SUM(event_copies)` shown alongside | `01`, `02` |
| Rankings (D3) | Top 10 categories (`category_id`) and brands (`brand`) by estimated revenue; the rest grouped as "other"; missing brand is a separate, never-ranked "unknown" row. Ties go to the lower ID / alphabetical brand. | `04` |
| Observed periods (D4) | 2019-11-21 to 2019-11-28 "observed peak" and 2019-12-26 to 2020-01-01 "observed trough" (dates from the Day 2 audit) are labels only. **No cause is claimed.** | `02` |

**Time attribution** (as used in SQL `02` and `03`):
- Events, revenue, visitors and buyers belong to the period in which the event happened (approved C7).
- Purchasing sessions belong to the period of the session's first valid purchase (approved A2).
- ✅ **Sessions belong to the period of the session's first event.** This follows the A2 rule so that monthly sessions add up exactly to the total. Approved 2026-11-24.
- Visitors and buyers are distinct counts: they **do not add up** across periods.

## 3a. Day 4 output column dictionary (approved 2026-11-24)

Output tables: `reports/day04_kpis/*.csv`, from `sql/01`–`sql/04`. **Additive** means the rows add up to the period total. Distinct counts are **not additive**: never sum them across rows (Excel / Power BI: use the period totals).

| Column | Table(s) | Definition | Additive across rows? |
|---|---|---|---|
| `est_revenue` | all | Estimated purchase revenue (A1) for the row | ✅ |
| `valid_purchase_events` | most | Purchase events with price > 0, merged duplicates once (= units at one per row) | ✅ |
| `events_dedup` / `events_raw` | overview, trends, event_counts | `COUNT(*)` / `SUM(event_copies)` (D2) | ✅ |
| `sessions` / `sessions_started` | trends, weekday × hour | Sessions counted in the period of their first event | ✅ (over time periods) |
| `purchasing_sessions` | overview, trends, weekday × hour | A2, counted in the period of the first valid purchase | ✅ over time periods · ❌ in category/brand tables |
| `visitors`, `buyers` | overview, trends, category/brand | Distinct users with any event / a valid purchase | ❌ |
| `distinct_products_purchased` | category, brand | Distinct products with ≥ 1 valid purchase in the row. **Not units.** A product listed under 2+ category IDs counts in each of those category rows. *(Renamed from `products_sold`, R1.)* | ❌ |
| `avg_price` | category, brand | `est_revenue ÷ valid_purchase_events`: the average price per unit bought, **not** an order value | — |
| `aov_proxy` | overview, monthly | `est_revenue ÷ purchasing_sessions` (proxy; see limitation below) | — |
| `revenue_per_visitor` | overview, monthly | `est_revenue ÷ visitors` | — |
| `revenue_share_pct` | weekday × hour, weekday, hour, category, brand | Row revenue ÷ **all** revenue in the table (brand table: **includes** the "unknown" row) | ✅ (≈100, rounding) |
| `share_of_known_brand_revenue_pct` | brand | Row revenue ÷ revenue **with a known brand**; empty for "unknown" *(R2)* | ✅ over known-brand rows |
| `est_revenue_from_filled_brand` | brand | The part of `est_revenue` whose brand was filled by C8 (missing in the raw data, filled from the product's single known brand). Always 0 for "unknown". | ✅ |
| `categories_in_row` / `brands_in_row` | category, brand | How many categories / brands the row covers (1 for ranked rows; more for "other") | ✅ |
| `dup_purchase_copies_all` | overview | Every merged duplicate purchase copy, at any price *(R3)* | — |
| `dup_purchase_copies_excluded`, `dup_purchase_value_excluded` | overview | Valid-price duplicate copies, and their value, left out of A1 revenue | — |
| `nonpositive_purchase_rows`, `nonpositive_purchase_value` | overview | Raw purchase rows with price ≤ 0, and their value (C4), reported separately | — |
| `n_days_utc` | weekday | Times each weekday occurs between the first and last event date (UTC), used for per-day averages | — |
| `observed_period` | daily | D4 label for the observed peak / trough; **no cause claimed** | — |
| `*_mom_pct` | monthly | Month-over-month change; empty when the previous month is 0 or absent | — |

**Reading the category and brand tables:**
- **Brand shares have two denominators.** `revenue_share_pct` sizes a brand against the whole business, including "unknown". `share_of_known_brand_revenue_pct` compares brands with each other. Missing brands carried about 40% of raw purchase revenue before the C8 fill (audit: 2,067,326.61), so the two can differ a lot, and "unknown" may be the largest single row. It is never ranked.
- **"Other"** contains only categories / brands **with** revenue. Categories and brands without valid purchases are counted only in `brand_coverage`.
- **Categories** are numeric store-taxonomy IDs. A readable `category_code` exists for few of them, and some of those are non-cosmetic (e.g. `appliances.environment.vacuum`).
- **Revenue rankings** show sales volume, not conversion performance (Day 5).

**Limitation: monthly AOV.** `aov_proxy` in the monthly table divides revenue by **event month** by purchasing sessions by **month of first valid purchase**. Sessions with valid purchases in two months (**670** in the Day 3 real data) put second-month revenue over a session counted in the earlier month, so monthly AOV is approximate. The **period-level** AOV has no such mismatch.

**Day 5 funnel decisions ✅ (approved 2026-11-24, F1–F7; to be implemented after the Day 4 real-data run is verified)**

| ID | Decision |
|---|---|
| F1 | **Session stage flags are independent:** `reached_view`, `reached_cart`, `reached_purchase` (valid) are set only by an event of that stage, never inferred from a later stage. Report conditional rates (cart ÷ view sessions, purchase ÷ cart sessions) **and** the share of purchasing sessions with no cart event. |
| F2 | **User-level funnel:** per user, for the whole period and per month: viewed, carted, made a valid purchase |
| F3 | **Segment funnels** use as denominator the **sessions that touched the segment** (any event in that category / brand / price band). Cart and purchase rates are measured against that. |
| F4 | **Price bands:** five bands from quintiles of each product's median positive price, computed at run time, with the band edges published |
| F5 | **Segments:** as Day 4: top 10 categories and brands by revenue, plus "other", plus "unknown" brand |
| F6 | **Statistics:** Wilson 95% confidence interval per rate; chi-square test across segments; each segment vs the rest by two-proportion z-test with Benjamini–Hochberg false-discovery-rate correction (q = 0.05); effect sizes in percentage points |
| F7 | **A difference is reported as meaningful only if** \|difference\| ≥ **1 percentage point** **and** it's significant after correction. Both conditions are shown. |
| — | Stated limitation: sessions of the same user aren't independent. Overall rates are re-estimated by resampling whole users as a robustness check. |
| — | Monthly session and user conversion (D1) are reported on Day 5. |

## 4. Cart abandonment window ⏳ (decision pending: options for review)

**Why a choice is needed.** The audit (C11) shows carts carry over between sessions:
- 18.6% of purchasing sessions contain no cart event.
- 50.6% of purchased session–product pairs were never carted **in that session**.

So "cart without purchase in the same session" will overstate abandonment by an amount we haven't measured yet.

| Option | Definition | Strengths | Weaknesses |
|---|---|---|---|
| **W1. Session** | Session with ≥ 1 cart and no valid purchase in that session | Simple, common in web analytics, needs no window | Counts carts bought in a later session as abandoned (overstates; size unknown) |
| **W2. User × product, fixed window N days** | A cart of product P by user U is abandoned if U doesn't buy P within N days of the cart event. Only carts with a full N-day follow-up before 2020-01-31 23:59:59 UTC are counted. | Follows the cart across sessions; deals with the end of the data honestly | N must be chosen; the last N days of carts are left out of the rate |
| **W3. User × product, to end of data** | Abandoned if U never buys P by 2020-01-31 | No window to choose | Late-January carts look abandoned (end-of-data bias); periods aren't comparable |
| **W4. User, fixed window N days** | User with ≥ 1 cart and no valid purchase of any product within N days | User-level view, useful for retargeting | Ignores which product was abandoned |

**How N would be chosen (if W2/W4), based on evidence rather than picked:**
1. Measure, on the clean data, the delay from each cart event to the same user's first valid purchase of that product, among carts that do convert.
2. Show its distribution (median, p75, p90, p95) and the conversion rate within 1, 3, 7, 14 and 30 days.
3. Pick N where further days add few conversions (e.g. the smallest N covering ≥ 90–95% of the conversions observed within 30 days). Report results for other N values too, as a sensitivity check.

**Also open for Day 6:** whether cart-leakage **value** multiplies by `event_copies` on cart/remove events. That depends on whether same-second copies are per-unit logging or double-fires. A targeted check compares the number of identical remove rows with the number of earlier cart events in the session.

**Suggested next step:** run the delay measurement (read-only) at the start of Day 6, present the figures, and decide then. No abandonment metric is calculated before that decision.

---

## Change log
| Date | Change |
|---|---|
| 2026-11-16 | Approved: UTC; order proxy; estimated revenue |
| 2026-11-24 | Day 4 audit follow-ups: column dictionary (§3a); duplicate-copies clarification; monthly AOV limitation; R1 `products_sold` → `distinct_products_purchased`; R2 `share_of_known_brand_revenue_pct`; R3 `dup_purchase_copies_all`. No change to revenue, purchasing-session or conversion calculations. |
| 2026-11-24 | Day 4 metrics approved (D1–D4). Session month attribution (first event) approved. Day 5 funnel decisions F1–F7 approved (F7: 1 pp + significant after correction). |
| 2026-11-18 | A1 (merged purchases counted once) and A2 (purchasing sessions, keyed, valid purchases only) approved. Cleaning conventions C1–C12 approved. Abandonment window left pending with options W1–W4. |
