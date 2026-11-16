# Data Dictionary: REES46 Cosmetics Shop Events (Oct 2019 – Jan 2020)

**Source file for every statistic:** `reports/day01_raw_profile.txt`, produced by `src/inspect_raw.py` (DuckDB, full-file type inference). Raw CSVs were not modified.

**Evidence labels** used throughout:

| Label | Meaning |
|---|---|
| **[O]** Observed | Copied directly from the profiling report |
| **[D]** Derived | Simple arithmetic on [O] values (sums, ratios) |
| **[A]** Assumption | Based on the dataset's published description or on reasoning, **not** proven by the report. Must be checked before we rely on it. |

---

## 1. Dataset overview

| File | Size on disk [O] | Rows [O] | First event [O] | Last event [O] |
|---|---|---|---|---|
| `2019-Oct.csv` | 482.5 MB | 4,102,283 | 2019-10-01 00:00:00 | 2019-10-31 23:59:54 |
| `2019-Nov.csv` | 545.8 MB | 4,635,837 | 2019-11-01 00:00:02 | 2019-11-30 23:59:58 |
| `2019-Dec.csv` | 415.3 MB | 3,533,286 | 2019-12-01 00:00:00 | 2019-12-31 23:59:57 |
| `2020-Jan.csv` | 501.8 MB | 4,264,752 | 2020-01-01 00:00:00 | 2020-01-31 23:59:58 |
| **Total** | **1,945.4 MB** [D] | **16,536,158** [D] | | |

- [O] Exactly these four files were profiled. No other months (e.g. Feb 2020) appear in the report.
- [O] All four files have the same 9 columns, in the same order, with the same inferred types.
- [O] Each event timestamp lies inside its own file's calendar month.
- [D] Each month's event-type counts sum exactly to that month's row count.
- **Grain** [A]: one row = one user interaction with one product at one point in time.

### Event mix

| event_type | Oct [O] | Nov [O] | Dec [O] | Jan [O] | 4-month total [D] | Share [D] |
|---|---|---|---|---|---|---|
| view | 1,862,164 | 2,076,132 | 1,728,331 | 2,037,608 | 7,704,235 | 46.59% |
| cart | 1,232,385 | 1,311,807 | 927,124 | 1,148,323 | 4,619,639 | 27.94% |
| remove_from_cart | 762,110 | 925,481 | 664,655 | 815,024 | 3,167,270 | 19.15% |
| purchase | 245,624 | 322,417 | 213,176 | 263,797 | 1,045,014 | 6.32% |
| **Total** | 4,102,283 | 4,635,837 | 3,533,286 | 4,264,752 | 16,536,158 | 100% |

---

## 2. Column summary

Null % and distinct counts are listed per month in the order **Oct / Nov / Dec / Jan**.

| # | Column | Type [O] | Null % [O] | Approx. distinct [O] |
|---|---|---|---|---|
| 1 | `event_time` | TIMESTAMP | 0.00 / 0.00 / 0.00 / 0.00 † | 1,766,678 / 1,779,264 / 1,610,557 / 1,799,402 |
| 2 | `event_type` | VARCHAR | 0.00 / 0.00 / 0.00 / 0.00 † | 4 / 4 / 4 / 4 |
| 3 | `product_id` | BIGINT | 0.00 / 0.00 / 0.00 / 0.00 † | 41,632 / 43,265 / 43,662 / 44,473 |
| 4 | `category_id` | BIGINT | 0.00 / 0.00 / 0.00 / 0.00 † | 485 / 483 / 475 / 480 |
| 5 | `category_code` | VARCHAR | **98.36 / 98.37 / 98.35 / 98.25** | 11 / 10 / 10 / 12 |
| 6 | `brand` | VARCHAR | **40.45 / 42.84 / 42.74 / 41.64** | 244 / 243 / 258 / 260 |
| 7 | `price` | DOUBLE | 0.00 / 0.00 / 0.00 / 0.00 † | 1,820 / 2,486 / 2,131 / 2,115 |
| 8 | `user_id` | BIGINT | 0.00 / 0.00 / 0.00 / 0.00 † | 407,809 / 367,152 / 362,047 / 389,980 |
| 9 | `user_session` | VARCHAR | 0.02 / 0.02 / 0.02 / 0.03 | 839,859 / 949,932 / 866,420 / 994,550 |

† **0.00 is rounded to two decimals.** It means fewer than 0.005% of rows are null, not that there are none. Exact null counts will be measured on Day 2.

**Distinct counts are approximate** [O]: DuckDB's `SUMMARIZE` reports `approx_unique`, a HyperLogLog estimate. Treat these as estimates within a few percent.

**Distinct counts can't be summed across months** [A]: the same users and products appear in several months. Exact 4-month distinct counts will be computed on Day 2.

---

## 3. Column details

### 1. `event_time`: TIMESTAMP
- **Meaning:** when the event happened.
- **Observed** [O]:
  - The raw text always carries a `UTC` suffix, e.g. `2019-12-01 00:00:00 UTC`.
  - DuckDB parses it into a time-zone-free TIMESTAMP.
  - Precision is to the second.
  - Ranges are within each calendar month (see §1).
- **Note** [D]: there are far fewer distinct timestamps than rows (e.g. 1,766,678 for 4,102,283 rows in Oct), so many events share the same second.
- **Assumption** [A]: the values are in UTC, which the suffix supports. The store's local time zone is not in the data.
- **Analytical use:**
  - time trends by month, week, weekday and hour
  - ordering events within a session
  - purchase dates for cohorts and RFM recency
  - time-based train/test split for the model

### 2. `event_type`: VARCHAR
- **Meaning:** the action the user took.
- **Observed** [O]: exactly 4 values in every month: `view`, `cart`, `remove_from_cart`, `purchase`. Counts are in §1.
- **Analytical use:**
  - defining the funnel stages
  - cart abandonment and remove-from-cart analysis (upgrade 1A is feasible: the event exists)
  - flagging purchases for revenue
  - the model's target label

### 3. `product_id`: BIGINT
- **Meaning:** product identifier.
- **Observed** [O]:
  - min 3752 in every month
  - max 5,900,470 (Oct) to 5,924,514 (Jan)
  - ≈41.6k–44.5k distinct per month
- **Analytical use:**
  - product-level funnel and leakage
  - detecting price changes per product (upgrade 1B feasibility check)
  - joining product attributes

### 4. `category_id`: BIGINT
- **Meaning:** product category identifier.
- **Observed** [O]:
  - 0.00% null in every month
  - ≈475–485 distinct categories per month
  - values reach about 2.24 × 10¹⁸, which fits BIGINT
- **Assumption** [A]: this is the most specific (leaf) category key. It's a number with no readable name.
- **Analytical use:**
  - **the main category dimension**, because `category_code` is almost always empty
  - grouping categories for the funnel, leakage and RFM breakdowns

### 5. `category_code`: VARCHAR
- **Meaning** [A]: readable category path, e.g. `accessories.bag`. Per the dataset description, it's filled only where a readable path could be built.
- **Observed** [O]:
  - **98.25–98.37% null**
  - only 10–12 distinct values per month
  - alphabetical min is `accessories.bag`; max is `stationery.cartrige` (the misspelling is in the source)
- **Analytical use:**
  - limited: a readable label for the few categories that have one
  - not usable as the main category dimension

### 6. `brand`: VARCHAR
- **Meaning:** product brand name.
- **Observed** [O]:
  - **40.45–42.84% null**
  - ≈243–260 distinct brands per month
  - lower-case in every sample row (e.g. `runail`, `f.o.x`, `grattol`)
- **Assumption** [A]: missing brand means unbranded or unknown products, not a data loss tied to particular events. To be checked on Day 2.
- **Analytical use:**
  - brand-level revenue and conversion, limited to rows with a brand
  - every brand result must state what share of events it covers

### 7. `price`: DOUBLE
- **Meaning:** product price at the time of the event.
- **Observed** [O]:
  - **min −79.37 in every month**
  - max 307.6 (Oct) and 327.78 (Nov, Dec, Jan)
  - 0.00% null
  - ≈1.8k–2.5k distinct values per month
- **Assumptions** [A]:
  - **Currency** isn't stated in the report; it's expected to be USD from the dataset description, to be confirmed.
  - **Quantity:** there's no quantity column, so each purchase row is assumed to be one unit.
- **Analytical use:**
  - revenue, as the sum of `price` over purchase events
  - average order value
  - price bands
  - leakage valuation of abandoned carts
  - price-sensitivity analysis (upgrade 1B)
  - the Monetary score in RFM

### 8. `user_id`: BIGINT
- **Meaning:** permanent customer identifier.
- **Observed** [O]:
  - 0.00% null
  - ≈362k–408k distinct users per month
  - the highest ID grows each month: 566,280,798 (Oct) → 608,822,072 (Jan)
- **Assumption** [A]: IDs are issued in sequence, so higher IDs are newer users. Not used in any analysis.
- **Analytical use:**
  - user-level funnel
  - cohorts by first purchase month
  - repeat-purchase rate
  - RFM
  - unique visitors and buyers

### 9. `user_session`: VARCHAR
- **Meaning:** session identifier. Per the dataset description [A], a new session starts when a user returns after a long pause.
- **Observed** [O]:
  - UUID format
  - **0.02–0.03% null**
  - ≈840k–995k distinct per month
  - in the Nov sample, one session holds a `cart` then a `remove_from_cart` by the same user (`553329724`)
- **Analytical use:**
  - session-level funnel
  - cart-abandonment unit (a session with a cart and no purchase)
  - order proxy (a session with at least one purchase)
  - unit of prediction for the propensity model

---

## 4. Not in the data (these affect the analysis)

| Missing field | Consequence | Workaround |
|---|---|---|
| `order_id` | Orders can't be identified directly | Treat a session with ≥1 purchase as one order [A]. Document this in the metric definitions. |
| `quantity` | Units sold are unknown | Count one unit per purchase row [A] |
| Traffic source, device, location | No acquisition or channel analysis | Stated as a limitation |
| Cost / margin | No profit analysis | Report revenue only |
| Product name | Products are known only by ID | Use the brand and category labels |

Data-quality issues and proposed fixes are in **`docs/data_quality_notes.md`**.
