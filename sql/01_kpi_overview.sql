-- =============================================================================
-- 01_kpi_overview.sql
-- Business question: How did the store perform over the whole period
--                    (Oct 2019 - Jan 2020, UTC)?
-- Definitions:  docs/metric_definitions.md
--   est_revenue          A1: SUM(price) over valid purchase events (price > 0),
--                        merged duplicate purchases counted once
--   purchasing_sessions  A2: distinct session_key with >= 1 valid purchase.
--                        A proxy for orders, NOT an exact order count.
--   visitors / buyers    distinct user_id with >= 1 event / >= 1 valid purchase
--   aov_proxy            est_revenue / purchasing_sessions (proxy)
--   conversion rates     approved for Day 4 at period level only (monthly: Day 5)
-- Source: view `events` = data/processed/events_clean (one row per
--         deduplicated event; event_copies = identical raw rows merged).
-- =============================================================================

-- name: kpi_overview
WITH t AS (
    SELECT
        MIN(event_time)                                                             AS period_start_utc,
        MAX(event_time)                                                             AS period_end_utc,
        COUNT(DISTINCT user_id)                                                     AS visitors,
        COUNT(DISTINCT user_id)     FILTER (WHERE event_type = 'purchase' AND is_valid_price) AS buyers,
        COUNT(DISTINCT session_key) FILTER (WHERE has_session)                      AS sessions,
        COUNT(DISTINCT session_key) FILTER (WHERE event_type = 'purchase' AND is_valid_price
                                              AND has_session)                      AS purchasing_sessions,
        COALESCE(SUM(CAST(price AS DECIMAL(18,2)))
                 FILTER (WHERE event_type = 'purchase' AND is_valid_price), 0)      AS est_revenue,
        COUNT(*)                    FILTER (WHERE event_type = 'purchase' AND is_valid_price) AS valid_purchase_events,
        COUNT(*)                                                                    AS events_dedup,
        SUM(event_copies)                                                           AS events_raw,
        -- reported separately (approved A1 / C4)
        COALESCE(SUM(event_copies - 1)
                 FILTER (WHERE event_type = 'purchase' AND is_valid_price), 0)      AS dup_purchase_copies_excluded,
        COALESCE(SUM(CAST(price AS DECIMAL(18,2)) * (event_copies - 1))
                 FILTER (WHERE event_type = 'purchase' AND is_valid_price), 0)      AS dup_purchase_value_excluded,
        COALESCE(SUM(event_copies)
                 FILTER (WHERE event_type = 'purchase' AND NOT is_valid_price), 0)  AS nonpositive_purchase_rows,
        COALESCE(SUM(CAST(price AS DECIMAL(18,2)) * event_copies)
                 FILTER (WHERE event_type = 'purchase' AND NOT is_valid_price), 0)  AS nonpositive_purchase_value
    FROM events
)
SELECT *,
       ROUND(est_revenue / NULLIF(purchasing_sessions, 0), 2)        AS aov_proxy,
       ROUND(est_revenue / NULLIF(visitors, 0), 4)                   AS revenue_per_visitor,
       ROUND(100.0 * purchasing_sessions / NULLIF(sessions, 0), 4)   AS session_conversion_pct,
       ROUND(100.0 * buyers / NULLIF(visitors, 0), 4)                AS user_conversion_pct
FROM t;

-- name: event_counts
-- Deduplicated (default, D2) and raw event counts by event type.
SELECT event_type,
       COUNT(*)                                             AS events_dedup,
       SUM(event_copies)                                    AS events_raw,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 4)   AS share_of_dedup_pct
FROM events
GROUP BY event_type
ORDER BY CASE event_type WHEN 'view' THEN 1 WHEN 'cart' THEN 2
                         WHEN 'remove_from_cart' THEN 3 ELSE 4 END;
