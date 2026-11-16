# E-commerce Funnel & Retention Diagnostics

> 🚧 Work in progress (Day 1 of 12). Executive summary will be added once the analysis is complete.

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
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
kaggle datasets files mkechinov/ecommerce-events-history-in-cosmetics-shop
# download the Oct 2019 – Jan 2020 files into data/raw (see docs for exact commands)
python src/inspect_raw.py data/raw --out reports/day01_raw_profile.txt
```

## Data
REES46 eCommerce events, cosmetics shop — Kaggle: `mkechinov/ecommerce-events-history-in-cosmetics-shop`.
