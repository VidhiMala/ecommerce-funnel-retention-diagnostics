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

## 3. Proposed definitions 🟡 (confirm on the day they are first used)

| Metric | Proposed definition | First used |
|---|---|---|
| Visitors | Distinct `user_id` with ≥ 1 event in the period | Day 4 |
| Buyers | Distinct `user_id` with ≥ 1 valid purchase | Day 4 |
| Average order value (proxy) | Estimated purchase revenue ÷ purchasing sessions | Day 4 |
| Revenue per visitor | Estimated purchase revenue ÷ visitors | Day 4 |
| Session conversion rate | Purchasing sessions ÷ sessions with `has_session` | Day 5 |
| Session stage flags | `reached_view`, `reached_cart`, `reached_purchase` (valid): whether the session contains ≥ 1 event of that stage. Each flag is independent: a purchase without a cart in the session is not counted as a cart. The share of such sessions is reported (audit: 18.6% of purchasing sessions have no cart). | Day 5 |
| User conversion rate | Buyers ÷ visitors, over a stated period | Day 5 |

---

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
| 2026-11-18 | A1 (merged purchases counted once) and A2 (purchasing sessions, keyed, valid purchases only) approved. Cleaning conventions C1–C12 approved. Abandonment window left pending with options W1–W4. |
