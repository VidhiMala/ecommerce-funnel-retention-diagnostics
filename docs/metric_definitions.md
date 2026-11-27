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

## 3b. Day 5 funnel ✅ (approved 2026-11-24 and 2026-11-27)

Implemented in `sql/05`–`sql/08` and `src/funnel.py`; statistics in `src/stats.py`.
Approvals: F1–F7 (2026-11-24); N1–N7, R, S and four corrections to the method (2026-11-27); two final corrections (2026-11-27). Where a later decision amends an earlier one, both are shown.

### Decisions

| ID | Decision |
|---|---|
| F1 | **Session stage flags are independent** ("at least one"): V = any view, C = any cart, P = any **valid** purchase (A2). A flag is set only by its own event type, never inferred. |
| F2 | **User-level funnel:** the same flags per user, for the whole period and per calendar month |
| F3 | **Segment funnels:** denominator = sessions that **touched** the segment (any event of a product in it); stages = cart / valid purchase **in** the segment |
| F4 | **Price bands** (as corrected 2026-11-27): (1) each product's **median positive price** over its deduplicated events; (2) quintile boundaries (20/40/60/80th percentiles) **across products**, one weight per product; (3) band *k* if boundary *k−1* < median ≤ boundary *k*, so identical medians share a band; (4) products with no positive price go in "no valid price": reported, never banded, excluded from tests. Events inherit their product's band. |
| F5 | **Segments:** top 10 categories / brands by estimated revenue (same rule as Day 4); every other category or known brand → "other"; missing brand → "unknown" (never ranked) |
| F6 | ~~Wilson CI; chi-square across segments; z-tests~~ **Superseded by S (2026-11-27)**: the user-clustered bootstrap below. Wilson intervals are kept only as a naive reference. |
| F7 | **Meaningful** = BH-adjusted p < 0.05 **and** \|difference\| ≥ **1 percentage point**. Both shown. |
| N1 | Three views: **stage reach** (share of all sessions), **strict nested funnel** V → V∩C → V∩C∩P (**primary**, so conversion + drop-off = 100%), and the **8-way path breakdown** |
| N2 | View and cart count at any price; purchase needs price > 0 |
| N3 | Primary funnel ignores order. Diagnostic: share of V∩C∩P sessions with first view ≤ first cart ≤ first purchase (same-second ties count as in order). |
| N4 | **Monthly session funnel:** each session, with all its flags, belongs to the month of its **first event** |
| N5 | User funnel over the whole period and calendar months; rows without a session are included at user level |
| N6 | Sensitivity (nothing removed): without sessions over 24 h; without the top 0.1% of sessions by events; first-day (2019-10-01) check |
| N7 / S | **User-clustered Poisson bootstrap**, B = 1,000, seed 20191001. **95% percentile CIs**; tests = Wald z with the **bootstrap SE**; **Benjamini–Hochberg** (q = 0.05) within each family; **no omnibus chi-square** |
| R | **Cross-session recovery: ordered is primary, unordered shown alongside** (see below) |

### Definitions

| Metric | Definition |
|---|---|
| Session conversion | \|P\| ÷ all sessions: identical to Day 4 |
| Strict view → cart | \|V∩C\| ÷ \|V\| |
| Strict cart → purchase | \|V∩C∩P\| ÷ \|V∩C\| |
| Strict view → purchase | \|V∩C∩P\| ÷ \|V\| |
| Drop-off at a stage | 100% − that stage's conversion |
| Cart → purchase, any view (F1 rate) | \|C∩P\| ÷ \|C\| |
| Purchases outside the strict funnel | \|P \ (V∩C)\| ÷ \|P\| |
| User conversion | buyers ÷ visitors (identical to Day 4); user strict rates as above, per user |
| **Carting users** | Users with ≥ 1 cart event **in a session**. A cart only in a row without a session doesn't count, because recovery needs sessions. |
| Within-session buyers | Carting users with ≥ 1 session containing both a cart and a valid purchase |
| **Unordered cross-session buyers** | Carting users with a cart in session *s₁* and a valid purchase in a **different** session *s₂* (`session_key` differs; same `user_id`) |
| **Ordered cross-session recovered** (primary) | As unordered, **and** the purchase is at or after the cart: first cart time in *s₁* ≤ last valid purchase time in *s₂* |
| Recovery rates | Each group ÷ carting users. **Ordered ⊆ unordered.** Lower bound: carts near 2020-01-31 have little time to be recovered. |
| Recovery identity | carting buyers = within-session buyers + unordered cross-session buyers − (users in both) |
| Monthly reconciliation | P(first-event month *m*) − P(first-purchase month *m*) = incoming(*m*) − outgoing(*m*). Incoming = purchasing sessions with first event in *m* but first valid purchase in another month; outgoing = first valid purchase in *m* but first event in another month. Σ incoming = Σ outgoing, so the totals are equal. |
| Segment rate (X) | Sessions touching X with a cart / valid purchase **in X** ÷ sessions touching X |
| Rest rate (X) | Sessions in the family **not** touching X with a cart / valid purchase on any family segment ÷ those sessions. Same bootstrap weights as X. |
| Design effect | (bootstrap SE)² ÷ naive binomial variance: how much clustering by user widens uncertainty |

### Validation (any failure stops `src/funnel.py`; nothing is published)
- **Matches Day 4:**
  - sessions, purchasing sessions, visitors, buyers, session and user conversion
  - monthly sessions, visitors, buyers, and purchasing sessions by first purchase
  - the top-10 category and brand lists
- **Consistency:**
  - each path breakdown adds up to its total
  - strict funnels never increase, and conversion + drop-off = 100%
- **Recovery:**
  - unordered and ordered are **each** computed two independent ways, and the two agree for every user
  - ordered ⊆ unordered
  - the recovery identity above holds
- **Monthly:** the identity above holds in every month; Σ incoming = Σ outgoing; totals equal
- **Price bands:**
  - boundaries strictly increasing
  - one band per product
  - banded + "no valid price" = all products
  - "no valid price" = products without a positive price
  - each band agrees with its product's median
- **Segments:** rates between 0% and 100%; segment sessions ≤ family sessions; category and brand families cover every session
- **Bootstrap** (corrected 2026-11-27: there is *no* requirement that the estimate lie inside its interval):
  - every rate is valid for each user
  - all-ones weights reproduce the exact totals
  - re-running the first batch with the same seed gives identical results
  - no failed resamples
  - interval bounds are finite, low ≤ high, and within [0, 1]
  - standard errors > 0
  - p-values are valid, and BH-adjusted p ≥ raw p
  - the "meaningful" flag follows F7
  - point estimates equal the SQL tables

### Day 5 output column dictionary (`reports/day05_funnel/`)

| File / column | Meaning |
|---|---|
| `session_stage_reach`, `user_stage_reach` | Stage reach (view A). `session_conversion_pct` / `user_conversion_pct` match Day 4. |
| `session_strict_funnel`, `user_strict_funnel` | Strict funnel (view B): `conversion_from_previous_pct`, `dropoff_from_previous_pct`, `conversion_from_view_pct` |
| `session_paths`, `user_paths` | All V/C/P combinations present; `share_pct` of the total |
| `session_funnel_metrics` | Supporting counts: purchases outside the strict funnel, without cart / view, cart sessions with a removal, single-event sessions |
| `order_diagnostic` | Time-order check of strict-funnel converters |
| `recovery_summary` | Carting users, carting buyers, within-session, unordered, ordered, overlap, rates |
| `monthly_session_funnel` | By first-event month: counts and rates, plus `purchasing_sessions_by_first_purchase`, `incoming`, `outgoing`, `difference` |
| `monthly_user_funnel` | Users active in each month and their stages |
| `category_funnel`, `brand_funnel`, `price_band_funnel` | Per segment: `sessions_touched`, `users_touched`, cart / purchase counts and rates, `rest_*` rates. **Not additive across rows.** |
| `price_band_boundaries`, `price_band_products`, `price_band_no_valid_price_sessions` | Band edges, products per band, and sessions touching unbanded products |
| `sensitivity`, `start_of_data_check` | N6 checks |
| `rates_with_ci.csv` | Every rate: `numerator`, `denominator`, `rate`, `ci_low`/`ci_high` (clustered percentile), `se_boot`, `wilson_low`/`wilson_high` (naive), `design_effect` |
| `comparisons.csv` | Every test: `diff_pp`, `diff_ci_low_pp`/`diff_ci_high_pp`, `se_boot_pp`, `z`, `p_value`, `p_adj_bh`, `significant_bh`, `meaningful` |

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
| 2026-11-27 | Day 5 funnel method finalised (§3b): N1–N7, R (ordered primary), S (clustered bootstrap; F6 superseded), F4 corrected (product medians → quintiles across products), recovery identity and monthly incoming/outgoing reconciliation, bootstrap checks without the inside-interval requirement. |
| 2026-11-24 | Day 4 audit follow-ups: column dictionary (§3a); duplicate-copies clarification; monthly AOV limitation; R1 `products_sold` → `distinct_products_purchased`; R2 `share_of_known_brand_revenue_pct`; R3 `dup_purchase_copies_all`. No change to revenue, purchasing-session or conversion calculations. |
| 2026-11-24 | Day 4 metrics approved (D1–D4). Session month attribution (first event) approved. Day 5 funnel decisions F1–F7 approved (F7: 1 pp + significant after correction). |
| 2026-11-18 | A1 (merged purchases counted once) and A2 (purchasing sessions, keyed, valid purchases only) approved. Cleaning conventions C1–C12 approved. Abandonment window left pending with options W1–W4. |
