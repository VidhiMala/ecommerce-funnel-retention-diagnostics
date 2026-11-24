-- =============================================================================
-- 02_monthly_daily_trends.sql
-- Business question: How do traffic, purchasing and revenue move month to month
--                    and day to day (UTC)?
-- Attribution (docs/metric_definitions.md):
--   events, revenue, visitors, buyers  -> the period in which the event happened
--   purchasing_sessions (A2)           -> period of the session's FIRST valid purchase
--   sessions                           -> period of the session's FIRST event
--   => revenue, sessions and purchasing_sessions sum exactly to the period totals;
--      visitors and buyers are distinct counts and are NOT additive across periods.
-- Conversion rates by month are deferred to Day 5 (decision D1).
-- Observed patterns (decision D4): labelled only; no cause is claimed. Dates come
--   from the Day 2 audit (reports/day02_audit/07_daily_2.csv, 07_daily_event_counts_full.csv).
-- =============================================================================

-- name: monthly_kpis
WITH ev AS (
    SELECT event_month                                                              AS month_utc,
           COUNT(DISTINCT user_id)                                                  AS visitors,
           COUNT(DISTINCT user_id) FILTER (WHERE event_type = 'purchase' AND is_valid_price) AS buyers,
           COALESCE(SUM(CAST(price AS DECIMAL(18,2)))
                    FILTER (WHERE event_type = 'purchase' AND is_valid_price), 0)   AS est_revenue,
           COUNT(*) FILTER (WHERE event_type = 'purchase' AND is_valid_price)       AS valid_purchase_events,
           COUNT(*)                                                                 AS events_dedup,
           SUM(event_copies)                                                        AS events_raw,
           COUNT(*) FILTER (WHERE event_type = 'view')                              AS views_dedup,
           COUNT(*) FILTER (WHERE event_type = 'cart')                              AS carts_dedup,
           COUNT(*) FILTER (WHERE event_type = 'remove_from_cart')                  AS removes_dedup,
           COUNT(*) FILTER (WHERE event_type = 'purchase')                          AS purchases_dedup
    FROM events GROUP BY 1
),
s AS (SELECT month_utc, COUNT(*) AS sessions FROM (
        SELECT strftime(MIN(event_time), '%Y-%m') AS month_utc
        FROM events WHERE has_session GROUP BY session_key) GROUP BY 1),
p AS (SELECT month_utc, COUNT(*) AS purchasing_sessions FROM (
        SELECT strftime(MIN(event_time), '%Y-%m') AS month_utc
        FROM events WHERE event_type = 'purchase' AND is_valid_price AND has_session
        GROUP BY session_key) GROUP BY 1)
SELECT ev.month_utc,
       ev.visitors, ev.buyers,
       COALESCE(s.sessions, 0)                                          AS sessions,
       COALESCE(p.purchasing_sessions, 0)                               AS purchasing_sessions,
       ev.est_revenue, ev.valid_purchase_events,
       ROUND(ev.est_revenue / NULLIF(COALESCE(p.purchasing_sessions, 0), 0), 2) AS aov_proxy,
       ROUND(ev.est_revenue / NULLIF(ev.visitors, 0), 4)                AS revenue_per_visitor,
       ev.events_dedup, ev.events_raw,
       ev.views_dedup, ev.carts_dedup, ev.removes_dedup, ev.purchases_dedup,
       ROUND(100.0 * (ev.est_revenue - LAG(ev.est_revenue) OVER w)
             / NULLIF(LAG(ev.est_revenue) OVER w, 0), 2)                AS revenue_mom_pct,
       ROUND(100.0 * (ev.visitors - LAG(ev.visitors) OVER w)
             / NULLIF(LAG(ev.visitors) OVER w, 0), 2)                   AS visitors_mom_pct,
       ROUND(100.0 * (COALESCE(p.purchasing_sessions, 0) - LAG(COALESCE(p.purchasing_sessions, 0)) OVER w)
             / NULLIF(LAG(COALESCE(p.purchasing_sessions, 0)) OVER w, 0), 2) AS purchasing_sessions_mom_pct
FROM ev LEFT JOIN s USING (month_utc) LEFT JOIN p USING (month_utc)
WINDOW w AS (ORDER BY ev.month_utc)
ORDER BY ev.month_utc;

-- name: daily_kpis
WITH ev AS (
    SELECT CAST(event_time AS DATE)                                                 AS day_utc,
           COUNT(DISTINCT user_id)                                                  AS visitors,
           COUNT(DISTINCT user_id) FILTER (WHERE event_type = 'purchase' AND is_valid_price) AS buyers,
           COALESCE(SUM(CAST(price AS DECIMAL(18,2)))
                    FILTER (WHERE event_type = 'purchase' AND is_valid_price), 0)   AS est_revenue,
           COUNT(*) FILTER (WHERE event_type = 'purchase' AND is_valid_price)       AS valid_purchase_events,
           COUNT(*)                                                                 AS events_dedup,
           SUM(event_copies)                                                        AS events_raw
    FROM events GROUP BY 1
),
s AS (SELECT day_utc, COUNT(*) AS sessions FROM (
        SELECT CAST(MIN(event_time) AS DATE) AS day_utc
        FROM events WHERE has_session GROUP BY session_key) GROUP BY 1),
p AS (SELECT day_utc, COUNT(*) AS purchasing_sessions FROM (
        SELECT CAST(MIN(event_time) AS DATE) AS day_utc
        FROM events WHERE event_type = 'purchase' AND is_valid_price AND has_session
        GROUP BY session_key) GROUP BY 1)
SELECT ev.day_utc,
       dayname(ev.day_utc)                                              AS weekday_utc,
       ev.visitors, ev.buyers,
       COALESCE(s.sessions, 0)                                          AS sessions,
       COALESCE(p.purchasing_sessions, 0)                               AS purchasing_sessions,
       ev.est_revenue, ev.valid_purchase_events, ev.events_dedup, ev.events_raw,
       CASE WHEN ev.day_utc BETWEEN DATE '2019-11-21' AND DATE '2019-11-28'
                THEN 'observed peak (2019-11-21 to 2019-11-28)'
            WHEN ev.day_utc BETWEEN DATE '2019-12-26' AND DATE '2020-01-01'
                THEN 'observed trough (2019-12-26 to 2020-01-01)' END   AS observed_period
FROM ev LEFT JOIN s USING (day_utc) LEFT JOIN p USING (day_utc)
ORDER BY ev.day_utc;
