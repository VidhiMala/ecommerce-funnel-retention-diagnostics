# Upgrade Plan — E-commerce Funnel & Retention Diagnostics

**Purpose:** turn a common portfolio project (REES46 funnel + RFM + cohorts) into one that stands out to hiring managers, without adding days to the 10-day timeline.

**Status:** Draft. Some upgrades depend on what the data actually contains. These are marked **⚠ verify on Day 1** and will be confirmed or dropped once we have the raw data profile.

---

## Summary

| # | Upgrade | Improves | Effort | Scheduled | Priority |
|---|---|---|---|---|---|
| 1 | One differentiating analysis | Originality, interview talking points | ~1 day (spread out) | Days 4–5 | Must |
| 2 | Statistical rigour (tests + confidence intervals) | Credibility, technical depth | ~0.5 day | Days 4–8 | Must |
| 3 | Executive one-pager + limitations | Storytelling, recruiter skim value | ~0.5 day | Day 10 | Must |
| 4 | One-command pipeline + data-quality tests | Reproducibility, engineering | ~0.5 day | Days 2–3, 10 | Should |
| 5 | Dashboard visibility (PDF/GIF, optional web app) | Deployability | ~0.5 day | Days 9–10 | Should |
| 6 | Metric definitions + cross-tool checks | Consistency, trust | ~1 hour | Days 3, 8–9 | Should |

---

## 1. One differentiating analysis

**What:** Add one analysis that most REES46 projects skip. There are two candidates; pick the one the data supports best:

- **A. Remove-from-cart behaviour:** Which products and price bands get removed from carts most? Does a removal in a session predict that the session ends without a purchase? Does removing one item and then buying another look like "trading down" to a cheaper product?
  ⚠ verify on Day 1: this needs `remove_from_cart` events in the data.
- **B. Price sensitivity:** Some products change price during the period. For those products, compare view→purchase conversion before and after each price change.
  ⚠ verify on Day 1: this needs enough products that change price, with enough traffic on each side of the change.

**Why:** It's the one finding a hiring manager remembers, and it gives you an obvious story to tell in interviews.

**Done when:** One section of the report and one dashboard visual answer a clear business question, with the method and its caveats explained.

---

## 2. Statistical rigour

**What:**
- **Chi-square / two-proportion z-tests:** Is the conversion difference between categories, brands or price bands real, or noise? Report p-values and effect sizes, not just "A > B".
- **Confidence intervals** on conversion rates (Wilson intervals).
- **Bootstrap confidence intervals** on the revenue-recovery estimates: resample users, recompute the estimate, and report a range.
- **Multiple-comparison control:** many segments get tested, so apply Benjamini–Hochberg so that false positives aren't presented as findings.

**Why:** "₹X–Y recoverable (95% CI)" reads as rigorous, while a single big number reads as made up. This separates an analyst from a dashboard-builder.

**Done when:** Every headline comparison in the report has a test or an interval next to it.

---

## 3. Executive one-pager + limitations

**What:** The top of the README and of the insights report gets:
- **3 key findings:** one sentence each, with the number.
- **3 recommendations:** what to do, the expected impact, and the main assumption behind it.
- **Limitations:** single store, Oct 2019–Jan 2020 only, short cohort window (the October cohort has 3 follow-up months), no marketing or cost data, and recovery estimates rest on scenario assumptions rather than measured outcomes.

**Why:** Recruiters skim for about 2 minutes. Stating limitations openly signals seniority; hiding them signals the opposite.

**Done when:** A non-technical reader understands the business story without scrolling past the first screen.

---

## 4. One-command pipeline + data-quality tests

**What:**
- `python -m src.pipeline` rebuilds everything: raw CSV → clean Parquet → analysis tables → exports for Excel and Power BI.
- `pytest` tests check rules such as:
  - no negative or zero prices remain after cleaning
  - no duplicate events
  - event times fall inside Oct 2019–Jan 2020
  - funnel counts never increase from one stage to the next
  - revenue totals from Python and SQL match
- Pinned dependencies in `requirements.txt`.
- *Optional:* GitHub Actions runs the tests on each push, using a small sample taken from the real data and committed to the repo.

**Why:** Shows you can build something reliable that other people can re-run, not a one-off notebook.

**Done when:** A fresh clone, the data download and one command reproduce every output.

---

## 5. Dashboard visibility

**What:**
- Export the Power BI dashboard to PDF and record a 30–60 s GIF walkthrough, both linked from the README.
- *If you have a work or school Power BI account:* use Publish to web. Free personal accounts usually can't.
- *Optional:* a small Streamlit app with the key funnel and RFM views, hosted for free on Streamlit Community Cloud.

**Why:** Most reviewers won't download a `.pbix` file. Whatever they can see in the README is what gets evaluated.

**Done when:** Someone can see the dashboard from the GitHub page without installing anything.

---

## 6. Metric definitions + cross-tool consistency

**What:**
- `docs/metric_definitions.md` defines every KPI exactly once. For example:
  - Is conversion counted per session or per user?
  - What counts as an abandoned cart?
  - How is revenue calculated: price × purchase events?
- Python and DuckDB are the only place metrics are calculated; Excel and Power BI read the exported tables.
- A reconciliation table checks that key totals match in Python, SQL, Excel and Power BI.

**Why:** In real teams, "why do these two numbers differ?" costs a lot of time. Showing you prevented it is a strong signal.

**Done when:** The reconciliation table shows matching totals across all four tools.

---

## Schedule

The day-by-day schedule lives in `CLAUDE.md` (12 days, Nov 16–27). The "Scheduled" column in the summary table above is from the earlier 10-day draft; CLAUDE.md wins if they differ.

---

## Principles (non-negotiable)

- Every number comes from the real downloaded data. Fill in placeholders only from actual outputs.
- If the data can't support an upgrade, drop it and record why. Don't force it.
- Depth beats breadth: one rigorous finding is worth more than ten shallow charts.
