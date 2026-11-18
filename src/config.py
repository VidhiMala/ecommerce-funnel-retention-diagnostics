"""
config.py - single source of truth for paths, raw-file expectations and schema.

Every module imports from here, so a path or expectation is defined once.
Values below come from the Day 1 profile (reports/day01_raw_profile.txt).
"""
from pathlib import Path

# --- Paths (relative to the project root; run modules from the root) --------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"      # caches; git-ignored via data/
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"  # cleaned Parquet (Day 2, after approval)
REPORTS_DIR = PROJECT_ROOT / "reports"

# --- Raw files in scope (Oct 2019 - Jan 2020), in chronological order -------
# Never rely on alphabetical file order (DQ-14): this list fixes the order.
RAW_FILES = ["2019-Oct.csv", "2019-Nov.csv", "2019-Dec.csv", "2020-Jan.csv"]
FILE_MONTH = {  # file -> first day of the month it must contain (UTC)
    "2019-Oct.csv": "2019-10-01",
    "2019-Nov.csv": "2019-11-01",
    "2019-Dec.csv": "2019-12-01",
    "2020-Jan.csv": "2020-01-01",
}

# Row counts observed in the Day 1 profile; the audit re-checks them.
EXPECTED_ROWS = {
    "2019-Oct.csv": 4_102_283,
    "2019-Nov.csv": 4_635_837,
    "2019-Dec.csv": 3_533_286,
    "2020-Jan.csv": 4_264_752,
}

# --- Raw schema ---------------------------------------------------------------
# event_time is read as text on purpose: we parse it with a strict format so
# rows that deviate from 'YYYY-MM-DD HH:MM:SS UTC' are counted, not hidden.
RAW_COLUMNS = {
    "event_time": "VARCHAR",
    "event_type": "VARCHAR",
    "product_id": "BIGINT",
    "category_id": "BIGINT",
    "category_code": "VARCHAR",
    "brand": "VARCHAR",
    "price": "DOUBLE",
    "user_id": "BIGINT",
    "user_session": "VARCHAR",
}
EVENT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
EVENT_TYPES = ["view", "cart", "remove_from_cart", "purchase"]

# --- Resource limits (16 GB laptop) ------------------------------------------
DUCKDB_MEMORY_LIMIT = "4GB"  # leaves room for Windows, browser, Power BI; DuckDB spills the rest to disk
