# Project memory: E-commerce Funnel & Retention Diagnostics

> **New session? Start here.** Read this whole file, then `docs/upgrade_plan.md`. Check **Status** at the bottom for the next step, greet the user with where we are, and continue. Don't redo finished steps.

This file carries the project context between sessions. Read it first; update **Status** at the end of every working day.

## Goal
An end-to-end data analytics portfolio project on real REES46 e-commerce clickstream data (Oct 2019 – Jan 2020). Analyses: business KPIs, conversion funnel, cart abandonment, cohort retention, RFM, revenue recovery scenarios, recommendations.

## Roles and rules
- Claude acts as senior Data Analyst, mentor and coding assistant. **The user builds the project**: Claude explains each module, the user runs it locally, and Claude verifies the output and debugs.
- Work step by step. Test each module before moving on.
- Never assume the data's structure: inspect the real files first.
- **Never fabricate data, results, statistics or insights.** Every number comes from real outputs.
- Code must be modular, documented, and bounded in memory: DuckDB for scanning, Parquet for storage.
- Python and DuckDB are the single source of metrics; Excel and Power BI read the exported tables.
- Depth and business insight over complexity.
- **Ask the user clarifying questions whenever an answer would change the work** (data choices, metric definitions, assumptions, tool access, presentation preferences). Don't guess silently; state any assumption explicitly.

## Stack
Python (pandas, numpy, scipy, statsmodels, scikit-learn), DuckDB, SQL, Excel, Power BI, Git/GitHub.

## Deliverables
1. Python pipeline (`python -m src.pipeline`) + pytest data-quality tests
2. 15+ SQL business queries in `sql/`
3. Excel what-if recovery model
4. 4-page Power BI dashboard (+ PDF export and GIF walkthrough)
5. Insights report with an executive one-pager and limitations
6. Public GitHub repo with README
7. **(DS)** Cart-session purchase-propensity model: logistic regression baseline vs gradient boosting, time-based split, calibration, feature importance, linked to recovery scenarios
8. **(PA)** A/B test plan for the top recommendation: hypothesis, primary metric, guardrails, power/sample-size calculation

## Timeline: deadline extended to **2026-12-05**, full scope (user decision 2026-11-24, Option A)
Originally 12 days ending Nov 27 (Day 1 = 2026-11-16). On 2026-11-24 the project was at Day 4 (≈5 days behind); the user chose to **extend the deadline and keep the full scope and quality standards: nothing removed or simplified.**
"Deployed" means: a public GitHub repo with CI, a README with the executive summary, the dashboard (PDF + GIF, plus Publish to web if allowed), a live Streamlit app, and a reproducible pipeline.
Every day ends with commit + push and an update to Status below. **Real data first:** every analysis day ends with the user's real-data run and Claude's verification; synthetic fixtures are only for tests.

| Day | Date | Tasks | Status |
|---|---|---|---|
| 1 | Nov 16 | GitHub repo; venv; download Oct–Jan; run profiler; data dictionary | ✅ |
| 2 | Nov 17–18 | Quality audit | ✅ |
| 3 | Nov 18 | Cleaning rules → Parquet → cleaning log; pipeline; tests; CI; metric definitions; data manifest | ✅ |
| 4 | Nov 24 | SQL 01–04: KPIs and trends (month/weekday/hour), top categories/brands | ✅ (real data 47/47, commit 33d4f57) |
| 5 | Nov 27 | SQL 05–08: session + user funnel, cross-session recovery, monthly funnel, category/brand/price-band funnels; user-clustered bootstrap, BH (chi-square dropped, decision S) | code ✅ · real-data run pending |
| 6 | Nov 26 | SQL 09–11: cart abandonment (window decided from measured cart→purchase delay), revenue leakage; differentiating analysis (1A or 1B) | |
| 7 | Nov 27 | SQL 12–16: cohort retention, repeat purchase, RFM segments + profiles | |
| 8 | Nov 28 | Propensity model: features, time split, logistic vs gradient boosting, calibration, importance | |
| 9 | Nov 29 | Bootstrap CIs on recovery; export tables; Excel what-if model; reconciliation check | |
| 10 | Nov 30 | Power BI 4-page dashboard | |
| — | Dec 1 | **Buffer** (Power BI overflow / catch-up) | |
| 11 | Dec 2 | Insights report, executive one-pager, limitations, A/B test plan; README | |
| 12 | Dec 3 | Streamlit app + deploy; PDF + GIF; Publish to web; v1.0 release candidate | |
| — | Dec 4–5 | **Buffer + final review**; v1.0 release | |

**Hard deadline: 2026-12-05.** Scope is fixed (user, 2026-11-24): if a day slips, use the buffer days first and raise it with the user before any cut. The original cut order (Streamlit → GIF → extra SQL beyond 15) applies only if the user approves a cut.

## User profile (answered 2026-11-15)
- Windows, 16 GB RAM, enough disk. Power BI Desktop is available.
- Self-rated 4.5/5 in Python, SQL, Excel and Power BI: keep explanations concise and focused on *why* and on trade-offs, not syntax basics.
- 4–6 hours a day available.
- Has GitHub, Kaggle and a work/school email (Power BI Service: try Publish to web; an org admin may have disabled it).
- Target roles: Data Analyst, Product Analyst, Data Scientist. The README and report should speak to all three.

## Decisions
- Data source: Kaggle `mkechinov/ecommerce-events-history-in-cosmetics-shop`, months Oct 2019 – Jan 2020. **Confirmed by the user.**
- Currency: report in the dataset's original currency (expected USD; verify on Kaggle page). No FX conversion: an exchange rate would add an unsourced assumption.
- Data work runs on the user's machine; the cloud session's network can't reach Kaggle.
- Upgrade plan: `docs/upgrade_plan.md` (must-do: #1, #2, #3).
- **Metric definitions, approved by the user 2026-11-16.** Copy these verbatim into `docs/metric_definitions.md` when it's created on Day 3:
  1. **Time zone:** all timestamps stay in UTC (the raw data carries a `UTC` suffix). Every time-based chart and table is labelled "UTC". No local-time conversion.
  2. **Order (proxy):** one purchasing session, i.e. a `user_session` containing ≥1 `purchase` event, counts as one order. Always state that this is a proxy, **not a verified order count**, because `order_id` is unavailable.
  3. **Estimated purchase revenue:** sum of `price` over `purchase` events where `price > 0`, assuming one unit per purchase row (no `quantity` column). Rows with `price ≤ 0` are excluded from this metric but **kept and flagged** in the dataset. The excluded record count and their summed value are always reported separately.
- **Cleaning gate (user rule, 2026-11-16):** no cleaning or modification of data until the user has seen the audit results and approved the proposed cleaning rules. Raw CSVs are never modified; cleaned output goes to new files only.
- Scope additions approved 2026-11-15: propensity model (DS) and A/B test plan (PA).
- **Deadline extended to 2026-12-05 with full scope (2026-11-24).** No component is removed or simplified without the user's explicit approval.

## Status
- [x] Project plan, roadmap and upgrade plan written
- [x] `src/inspect_raw.py` written and smoke-tested (on a dummy file only)
- [x] Day 1 scaffold: folders, .gitignore, requirements.txt, README stub
- [x] Day 1: GitHub repo created: `VidhiMala/ecommerce-funnel-retention-diagnostics` (public), starter files committed
- [x] Day 1: environment set up, data downloaded (4 files, 16,536,158 rows), raw profile pushed (`reports/day01_raw_profile.txt`)
- [x] Day 1: `docs/data_dictionary.md` and `docs/data_quality_notes.md` written; every figure traced to the report
- [x] Day 1 close-out: user reviewed both docs and approved the three metric definitions. **Day 1 approved 2026-11-16.**
- [x] Day 2 step 1a: `src/config.py`, `src/audit_raw.py` (read-only audit, 11 checks) and `tests/test_audit_raw.py` (14 tests on a synthetic fixture) written. Full-scale synthetic run (16.5M rows): ~60s, peak memory ~4.3 GB at the 4 GB DuckDB limit; output deterministic across runs.
- [x] Day 2 step 1b: user ran the audit locally and pushed `reports/day02_audit_report.md` + `reports/day02_audit/` (commit fdc0492). Claude verified it: 24/24 checks pass against the Day 1 profile and for internal consistency. **Day 2 closed by the user 2026-11-18.** The cleaning rules, Parquet output and cleaning log move to Day 3.
- [x] Day 3 step 1: cleaning rules C1–C12 + A1, A2 **approved by the user 2026-11-18** (A2 wording: "purchasing sessions", not an exact order count). Step 7 (data manifest) approved.
- [x] Day 3 code (Claude, tested on synthetic data only): `docs/cleaning_rules.md`, `src/clean.py` (37 validations; output replaced only if all pass), `src/manifest.py`, `src/pipeline.py`, `docs/metric_definitions.md` (abandonment window **pending**: options W1–W4), CI workflow, README reproduction steps. Tests: 34 pass + 12 real-data tests that skip without data. Full-scale synthetic run: clean ~96s, peak ~4.3 GB.
- [x] Day 3 user run (commit 81cf6a2): manifest created; cleaning 37/37 validations PASS; 46/46 tests pass; CI green. Claude cross-checked the real outputs against the Day 2 audit: all consistent. **Day 3 closed 2026-11-18. Schedule is back on track** (Day 3 finished on its planned date).
- [x] Day 4 plan approved 2026-11-24 (D1: overall conversion on Day 4, monthly on Day 5; D2: dedup counts default + raw alongside; D3: top 10 by revenue + 'other', 'unknown' brand separate; D4: Nov 21–28 peak and Dec 26–Jan 1 trough labelled as observed only, no cause).
- [x] Day 4 code (Claude, synthetic data only): `sql/01`–`04` (10 named queries), `src/kpis.py` (40 validations incl. Day 3 reconciliation; publishes only if all pass), pipeline step `kpis`, `tests/test_kpis.py`. Full-scale synthetic: KPIs ~14s, deterministic. **Bug found by the Day 4 reconciliation and fixed in `src/clean.py`:** monthly A2 counted a NULL session group when a valid purchase had no session. Real data unaffected (no such purchases); a validation (38 total) and a regression test were added.
- [x] Day 4 audit (Claude, SQL review 2026-11-24): definitions correct. User approved docs 1–4 and code R1–R3. Implemented: `products_sold` → `distinct_products_purchased`; `share_of_known_brand_revenue_pct` (brand table); `dup_purchase_copies_all` (overview); column dictionary `docs/metric_definitions.md` §3a; report reader notes. KPI validations now 47 (original 40 unchanged + 7). Synthetic full-scale check: every pre-existing value identical. 46 tests pass + 13 real-data skips.
- [x] Day 4 user run (commit 33d4f57): KPIs 47/47 on real data; Day 3 regenerated (38/38; only the new check differs). **Day 4 completed (user, 2026-11-27).**
- [x] **Schedule:** user chose Option A on 2026-11-24: deadline extended to **2026-12-05**, full scope kept. Timeline table updated.
- [x] Day 5 plan approved 2026-11-24: F1–F7 (F7 = ≥1 pp **and** significant after BH correction) + session month attribution (first event). Recorded in `docs/metric_definitions.md` §3.
- [x] Day 5 method finalised 2026-11-27 (`docs/metric_definitions.md` §3b): N1–N7; R = ordered cross-session recovery primary, unordered alongside; S = user-clustered Poisson bootstrap B=1,000, percentile CIs, bootstrap-SE Wald tests, BH, no omnibus chi-square. User corrections: recovery by distinct users across different session_keys; monthly difference = incoming − outgoing; clustered tests; product-median price bands. Final corrections: no "estimate inside CI" check; recovery identity uses within-session + unordered − overlap, ordered ⊆ unordered.
- [x] Day 5 code (Claude, synthetic data only): `src/stats.py` (+14 unit tests, written first), `sql/05`–`08`, `src/funnel.py` (50 validations; publishes only if all pass), pipeline step `funnel`, `tests/test_funnel.py` (hand-worked SQL fixture + full-pipeline fixture). Tests: 72 pass + 14 real-data skips. Bootstrap benchmark at 1.34M users, B=1,000: first design 376 s / 8.7 GB for one batch of 20 → rebuilt as linear combinations of sparse base counts with float32 resampling: 57 s / 2.0 GB. Full funnel step on a realistic 13.8M-event synthetic set: 117 s, peak 4.24 GB (DuckDB closed before Python arrays; inputs handed over via `data/interim/funnel_inputs/`), 50/50 checks, deterministic.
- [x] Day 5 audit (user's real-data report, 2026-11-27, NOT committed): (1) the "strict" funnel checked presence, not order → renamed **nested (presence-based)**, still primary; **chronological session funnel** added (cart at/after first view, valid purchase at/after that cart; ties ordered; two independent computations); diagnostic relabelled **first-occurrence order** + `with_chronological_path`. (2) p_adj_bh "0" came from report rounding (<5e-5 → "0") and float underflow (|z| > ~37.5) → p-values now in **log space** (`log10_p_value`, `log10_p_adj_bh`), BH in log space = statsmodels where representable, display "<0.0001". Also fixed: stale CSVs from earlier runs (e.g. `session_strict_funnel.csv`) are now removed when a run publishes. Tests 81 pass + 14 real-data skips. Full-scale synthetic: 63/63 checks, all existing values identical to the committed version (p-values within 1.5e-15 relative; flags identical), Day 4 reconciliation intact, 124–161 s, peak 4.27–4.28 GB. **Not committed or pushed (user rule).**
- [ ] Day 5 user run: `git pull` → `python -m pytest` → `python -m src.pipeline --steps verify,funnel` → `python -m pytest` → push `reports/day05_funnel_report.md` + `reports/day05_funnel/` → Claude verifies and writes the findings note  ← **NEXT**
- [ ] Open decisions: cart-abandonment window (W1–W4, decide at the start of Day 6 after measuring cart→purchase delay); whether cart-leakage value uses `event_copies` (Day 6); (session month attribution and Day 5 metrics: approved 2026-11-24)

## Conversation log (key points, newest last)
- **2026-11-15:**
  - Project defined: 7 analytical objectives; deliverables are the pipeline, 15+ SQL, Excel, a 4-page Power BI dashboard, the report and the repo.
  - The cloud session can't reach Kaggle or REES46, so data work runs on the user's Windows machine and the user pastes outputs back for review.
  - Dataset choice: cosmetics shop (Oct–Jan) over multi-category, because Kaggle has Oct–Jan for it, it's laptop-sized and it has `remove_from_cart` events.
  - Project rated ~6/10 as planned, ~8/10 with upgrades. Weakest areas: originality and deployability. Upgrade plan written.
  - User answers: Windows, 16 GB RAM, 4.5/5 skills, 4–6 hrs/day, has GitHub/Kaggle/work email, targets DA + PA + DS.
  - Approved additions: propensity model (DS) and A/B test plan (PA).
  - Currency: keep the dataset's original currency, no FX conversion.
  - Standing rule: ask the user whenever an answer would change the work.
- **2026-11-16 (Day 1):**
  - Scaffold created and tested: `.gitignore`, `requirements.txt` (`six` added because kaggle 2.x needs it), README stub, folders.
  - `inspect_raw.py` gained `--out` (UTF-8 report), because shell redirection on Windows breaks the table borders.
  - Kaggle CLI 2.x login is `kaggle auth login`, not `kaggle.json`.
  - User asked whether 10 days at 4–5 hrs is enough: no. User chose **12 days, full scope, finishing Nov 27**.
  - Repo created by the user: `VidhiMala/ecommerce-funnel-retention-diagnostics`. Checked it was empty before the first commit.
  - Data downloaded and profiled by the user: Oct 4,102,283 · Nov 4,635,837 · Dec 3,533,286 · Jan 4,264,752 rows; total 16,536,158. Identical 9-column schema in all files.
  - Key findings: `category_code` 98.25–98.37% null (use `category_id`); `brand` 40.45–42.84% null; min price −79.37 every month; `user_session` 0.02–0.03% null; cart events = 54–66% of views (use "ever reached" funnels, not event ratios); no `order_id` or `quantity` column; timestamps are UTC; Dec volume lower (cause to check); `remove_from_cart` exists, so upgrade 1A is feasible.
  - Proposed defaults (not yet applied): keep UTC; order = a session with ≥1 purchase; revenue = sum of price over valid purchases; exclude price ≤ 0 from revenue.
  - User reviewed and approved the data dictionary and quality notes. Approved metric definitions: UTC; order = purchasing session (proxy, not verified); revenue = positive purchase prices, 1 unit per row, non-positive prices kept and flagged and reported separately. Day 1 closed.
- **2026-11-18 (Day 2, started one day late; Day 2 was scheduled for Nov 17):**
  - `requirements-lock.txt` converted from UTF-16 (PowerShell `>`) to UTF-8; same contents. The user's versions include duckdb 2.0.0 and pandas 3.1.1.
  - The audit stages the CSVs into `data/interim/raw_stage.duckdb`: a typed copy with values unchanged (not cleaning). Raw CSVs are read-only.
  - DuckDB memory limit lowered from 6 GB to 4 GB to leave room on the 16 GB laptop.
  - Reports never print absolute paths, which keeps local user names out of the public repo.
  - Audit headline facts (verified): 0 timestamp problems, 0 near-duplicates, 0 empty strings, 0 zero-event hours; 868,808 exact-duplicate extra rows (782,059 of them `remove_from_cart`); 94 negative-price rows on 5 products that never have a positive price; 32,903 zero-price rows; 3,543 null-session rows (no purchases among them); 191 sessions with 2+ users; brand back-fillable rows 54,537; 1,614 products in 2+ category_ids; Dec 26–Jan 1 low-volume trough; raw revenue 5,144,461.71 over 1,044,922 positive-price purchase rows.
  - User approved C1–C12, A1 and A2 (purchasing sessions = `user_id`+`user_session` with ≥1 valid purchase; never called an exact order count) and the data manifest. Extra requirements: raw untouched; `SUM(event_copies)` = 16,536,158; keep raw and deduplicated counts; nothing silently discarded; revenue consistent before and after; stop on any validation failure; abandonment window not finalised without presenting options.
  - Testing found and fixed: SQL alias error; results whose order could change between runs (UNION ALL); counts printed as floats; a NaN in the data-quality test. A synthetic month-boundary bug was correctly caught by validation (output refused).
  - Day 3 real results: 15,667,350 deduplicated rows (`SUM(event_copies)` = 16,536,158); estimated purchase revenue (A1) 5,141,266.90 (raw 5,144,461.71 − 3,194.81 from 789 valid-price duplicate copies; the other 4 of the 793 duplicate purchase copies have non-positive prices); non-positive purchases 92 / −2,746.05; purchasing sessions (A2) 126,503. Splitting shared IDs and excluding non-positive purchases changed none of them, so all 126,503 contain a valid purchase. By month of first valid purchase: 29,326 / 36,377 / 28,687 / 32,113. 670 purchasing sessions have purchases in two months and are counted once. Session keys 3,614,959 (+234 from splitting 191 shared IDs). Parquet 448.1 MB.
- **2026-11-24 (Day 4):** the user approved D1–D4. Day 4 SQL, runner and tests built and pushed. The Day 4 reconciliation exposed a Day 3 monthly A2 bug (fixed, with no effect on real data). Schedule warning raised: 5 days behind.
- **2026-11-24:** user approved Option A: deadline 2026-12-05, original scope and quality kept, real-data runs prioritised. Timeline rewritten with buffers on Dec 1 and Dec 4–5.
- **2026-11-24:** user approved F1–F7 (1 pp practical threshold plus significance after correction) and session month attribution. Day 5 implementation is gated on the verified Day 4 real-data run.
- **2026-11-24:** Day 4 SQL audit done. R1–R3 and docs 1–4 approved and implemented, with calculations unchanged. The user's real Day 4 results have still not been pushed; Day 4 isn't closed until they're reviewed.
- **2026-11-27:** Day 4 declared complete by the user (real results verified: revenue 5,141,266.90, purchasing sessions 126,503, session conversion 3.4994%, user conversion 6.9066%). Day 5 method finalised and implemented. Schedule: Day 5 started 2 days late; buffer days (Dec 1, Dec 4–5) absorb it.
