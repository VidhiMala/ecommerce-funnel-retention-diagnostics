# E-commerce Funnel & Retention Diagnostics

> 🚧 Work in progress (Day 5 of 12). Executive summary will be added once the analysis is complete.

End-to-end analysis of real e-commerce clickstream data (REES46 cosmetics store, Oct 2019 – Jan 2020):
conversion funnel, cart abandonment, cohort retention, RFM segmentation, purchase-propensity modelling,
revenue-recovery scenarios and an A/B test plan.

**Stack:** Python · DuckDB · SQL · pandas · SciPy/Statsmodels · scikit-learn · Excel · Power BI

## Repository structure
```
data/raw/         raw monthly CSVs (not committed)
data/processed/   cleaned Parquet (not committed)
src/              Python pipeline modules
sql/              business SQL queries
notebooks/        exploration only
tests/            data-quality tests (pytest)
excel/            what-if recovery model
powerbi/          dashboard (.pbix) + exports
reports/          profiles, figures, insights report
docs/             plans and metric definitions
```

## Reproduce

Requires Python 3.11 or 3.12. Commands are for Windows PowerShell; on macOS/Linux activate with `source .venv/bin/activate`.

**1. Environment**
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**2. Download the four raw files** (Kaggle account needed; log in once with `kaggle auth login`)
```bash
kaggle datasets download mkechinov/ecommerce-events-history-in-cosmetics-shop -f 2019-Oct.csv -p data/raw --unzip
kaggle datasets download mkechinov/ecommerce-events-history-in-cosmetics-shop -f 2019-Nov.csv -p data/raw --unzip
kaggle datasets download mkechinov/ecommerce-events-history-in-cosmetics-shop -f 2019-Dec.csv -p data/raw --unzip
kaggle datasets download mkechinov/ecommerce-events-history-in-cosmetics-shop -f 2020-Jan.csv -p data/raw --unzip
```
Only these four monthly files are in scope. Other files in the dataset are not used.

**3. Verify the files are exactly the ones analysed** (size, row count and SHA-256 in `docs/data_manifest.md`)
```bash
python -m src.manifest verify
```
If this reports a mismatch (for example a newer dataset version on Kaggle), stop: results won't be comparable.

**4. Rebuild everything** (verify → audit → clean → kpis → funnel; later stages are added as the project grows)
```bash
python -m src.pipeline
```

**5. Tests**
```bash
python -m pytest
```
Tests on real data skip automatically when the cleaned data isn't present.

## Documentation
| File | Contents |
|---|---|
| `docs/data_dictionary.md` | Columns, types, meaning, observed quality |
| `docs/data_quality_notes.md` | Issues found in profiling (DQ-01 to DQ-14) |
| `docs/cleaning_rules.md` | Approved cleaning rules (C1–C12, A1, A2) |
| `docs/metric_definitions.md` | Approved, proposed and pending metric definitions |
| `docs/data_manifest.md` | Exact raw files: size, rows, SHA-256 |
| `reports/day02_audit_report.md` | Raw-data audit results |
| `reports/day03_cleaning_log.md` | Cleaning results and validations |
| `sql/01`–`sql/04` | Business performance queries (KPIs, trends, weekday/hour, categories/brands) |
| `reports/day04_kpi_report.md` | Day 4 KPI results and validations |
| `sql/05`–`sql/08` | Funnel queries: session/user funnels, cross-session recovery, monthly, category/brand/price-band funnels |
| `src/stats.py` | User-clustered bootstrap, Benjamini–Hochberg, Wilson reference intervals |
| `reports/day05_funnel_report.md` | Day 5 funnel results, confidence intervals, tests and validations |

## Data
REES46 eCommerce events, cosmetics shop — Kaggle: `mkechinov/ecommerce-events-history-in-cosmetics-shop`.
