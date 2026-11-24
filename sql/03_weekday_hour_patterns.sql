-- =============================================================================
-- 03_weekday_hour_patterns.sql
-- Business question: On which weekdays and at which hours (UTC) do sessions,
--                    purchasing sessions and revenue occur?
-- All times UTC (approved); the store's local time zone is unknown, so local
--   shopping hours must not be inferred from these tables.
-- Attribution as in 02: revenue by event time; sessions by first event;
--   purchasing_sessions by first valid purchase. All three sum to the totals.
-- Weekdays occur a different number of times in the period, so per-day averages
--   (n_days_utc) are given for fair comparison.
-- =============================================================================

-- name: weekday_hour
WITH grid AS (
    SELECT w.wd AS weekday_num, h.hr AS hour_utc
    FROM range(1, 8) w(wd), range(0, 24) h(hr)
),
ev AS (
    SELECT isodow(event_time) AS weekday_num, hour(event_time) AS hour_utc,
           COUNT(*)                                                                 AS events_dedup,
           COALESCE(SUM(CAST(price AS DECIMAL(18,2)))
                    FILTER (WHERE event_type = 'purchase' AND is_valid_price), 0)   AS est_revenue,
           COUNT(*) FILTER (WHERE event_type = 'purchase' AND is_valid_price)       AS valid_purchase_events
    FROM events GROUP BY 1, 2
),
s AS (SELECT isodow(t0) AS weekday_num, hour(t0) AS hour_utc, COUNT(*) AS sessions_started
      FROM (SELECT MIN(event_time) AS t0 FROM events WHERE has_session GROUP BY session_key) GROUP BY 1, 2),
p AS (SELECT isodow(t0) AS weekday_num, hour(t0) AS hour_utc, COUNT(*) AS purchasing_sessions
      FROM (SELECT MIN(event_time) AS t0 FROM events
            WHERE event_type = 'purchase' AND is_valid_price AND has_session GROUP BY session_key) GROUP BY 1, 2)
SELECT g.weekday_num,
       ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][g.weekday_num] AS weekday_utc,
       g.hour_utc,
       COALESCE(ev.events_dedup, 0)                    AS events_dedup,
       COALESCE(s.sessions_started, 0)                 AS sessions_started,
       COALESCE(p.purchasing_sessions, 0)              AS purchasing_sessions,
       COALESCE(ev.est_revenue, 0)                     AS est_revenue,
       COALESCE(ev.valid_purchase_events, 0)           AS valid_purchase_events,
       ROUND(100.0 * COALESCE(ev.est_revenue, 0) / NULLIF(SUM(COALESCE(ev.est_revenue, 0)) OVER (), 0), 4) AS revenue_share_pct
FROM grid g
LEFT JOIN ev USING (weekday_num, hour_utc)
LEFT JOIN s  USING (weekday_num, hour_utc)
LEFT JOIN p  USING (weekday_num, hour_utc)
ORDER BY g.weekday_num, g.hour_utc;

-- name: weekday_summary
WITH days AS (
    SELECT isodow(d) AS weekday_num, COUNT(*) AS n_days_utc
    FROM (SELECT CAST(generate_series AS DATE) AS d FROM generate_series(
            (SELECT CAST(MIN(event_time) AS DATE) FROM events)::TIMESTAMP,
            (SELECT CAST(MAX(event_time) AS DATE) FROM events)::TIMESTAMP, INTERVAL 1 DAY))
    GROUP BY 1
),
wh AS (SELECT weekday_num, ANY_VALUE(weekday_utc) AS weekday_utc,
              SUM(events_dedup) AS events_dedup, SUM(sessions_started) AS sessions_started,
              SUM(purchasing_sessions) AS purchasing_sessions, SUM(est_revenue) AS est_revenue
       FROM q_weekday_hour GROUP BY 1)
SELECT wh.weekday_num, wh.weekday_utc, days.n_days_utc,
       wh.events_dedup, wh.sessions_started, wh.purchasing_sessions, wh.est_revenue,
       ROUND(wh.est_revenue / days.n_days_utc, 2)                         AS avg_revenue_per_day,
       ROUND(wh.purchasing_sessions / days.n_days_utc, 2)                 AS avg_purchasing_sessions_per_day,
       ROUND(100.0 * wh.est_revenue / SUM(wh.est_revenue) OVER (), 4)     AS revenue_share_pct
FROM wh JOIN days USING (weekday_num)
ORDER BY wh.weekday_num;

-- name: hour_summary
SELECT hour_utc,
       SUM(events_dedup)          AS events_dedup,
       SUM(sessions_started)      AS sessions_started,
       SUM(purchasing_sessions)   AS purchasing_sessions,
       SUM(est_revenue)           AS est_revenue,
       ROUND(100.0 * SUM(est_revenue) / SUM(SUM(est_revenue)) OVER (), 4)          AS revenue_share_pct,
       ROUND(100.0 * SUM(purchasing_sessions) / SUM(SUM(purchasing_sessions)) OVER (), 4) AS purchasing_sessions_share_pct
FROM q_weekday_hour
GROUP BY hour_utc
ORDER BY hour_utc;
