-- =============================================================================
-- 05_funnel_overall.sql
-- Business questions: Where do sessions and users drop out between view, cart and
--   purchase? How much buying happens in a different session from the cart?
--   How stable is conversion month to month (UTC)?
-- Approved decisions (docs/metric_definitions.md §3b): F1-F7, N1-N7, R, S.
--   Flags per session: V = any view, C = any cart, P = any VALID purchase (A2).
--   Flags are independent ("at least one"); order is ignored in the primary funnel.
--   Strict funnel = nested sets V -> V&C -> V&C&P.  Rows without a session are excluded
--   at session level and included at user level.  Purchases need price > 0; views and
--   carts count at any price.
-- Tables whose name starts with "_" are internal (bootstrap inputs) and not exported.
-- =============================================================================

-- name: _session_flags
SELECT session_key,
       MIN(user_id)                                                               AS user_id,
       MIN(event_time)                                                            AS first_event_utc,
       bool_or(event_type = 'view')                                               AS v,
       bool_or(event_type = 'cart')                                               AS c,
       bool_or(event_type = 'purchase' AND is_valid_price)                        AS p,
       bool_or(event_type = 'remove_from_cart')                                   AS r,
       MIN(event_time) FILTER (WHERE event_type = 'view')                         AS first_view_utc,
       MIN(event_time) FILTER (WHERE event_type = 'cart')                         AS first_cart_utc,
       MIN(event_time) FILTER (WHERE event_type = 'purchase' AND is_valid_price)  AS first_purchase_utc,
       MAX(event_time) FILTER (WHERE event_type = 'purchase' AND is_valid_price)  AS last_purchase_utc,
       COUNT(*)                                                                   AS events_dedup,
       bool_or(session_over_24h)                                                  AS over_24h,
       COUNT(DISTINCT user_id)                                                    AS users_in_session
FROM events
WHERE has_session
GROUP BY session_key;

-- name: session_stage_reach
-- View A: share of ALL sessions reaching each stage (independent flags).
SELECT COUNT(*)                                            AS sessions,
       COUNT(*) FILTER (WHERE v)                           AS sessions_with_view,
       COUNT(*) FILTER (WHERE c)                           AS sessions_with_cart,
       COUNT(*) FILTER (WHERE p)                           AS sessions_with_valid_purchase,
       ROUND(100.0 * COUNT(*) FILTER (WHERE v) / COUNT(*), 4) AS view_reach_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE c) / COUNT(*), 4) AS cart_reach_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE p) / COUNT(*), 4) AS session_conversion_pct
FROM q__session_flags;

-- name: session_strict_funnel
-- View B (primary): nested sets, so conversion + drop-off = 100% at every stage.
WITH f AS (
    SELECT COUNT(*) FILTER (WHERE v)              AS s1,
           COUNT(*) FILTER (WHERE v AND c)        AS s2,
           COUNT(*) FILTER (WHERE v AND c AND p)  AS s3
    FROM q__session_flags
)
SELECT * FROM (
    SELECT 1 AS stage_order, 'view' AS stage, s1 AS sessions, NULL::DOUBLE AS conversion_from_previous_pct,
           NULL::DOUBLE AS dropoff_from_previous_pct, 100.0 AS conversion_from_view_pct FROM f
    UNION ALL
    SELECT 2, 'view + cart', s2, ROUND(100.0 * s2 / s1, 4), ROUND(100.0 - 100.0 * s2 / s1, 4),
           ROUND(100.0 * s2 / s1, 4) FROM f
    UNION ALL
    SELECT 3, 'view + cart + valid purchase', s3, ROUND(100.0 * s3 / s2, 4), ROUND(100.0 - 100.0 * s3 / s2, 4),
           ROUND(100.0 * s3 / s1, 4) FROM f
) ORDER BY stage_order;

-- name: session_paths
-- View C: all 8 combinations of the flags; sums to all sessions.
SELECT v AS has_view, c AS has_cart, p AS has_valid_purchase,
       COUNT(*)                                        AS sessions,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS share_pct
FROM q__session_flags
GROUP BY ALL
ORDER BY has_view DESC, has_cart DESC, has_valid_purchase DESC;

-- name: session_funnel_metrics
-- Counts behind the headline session rates (confidence intervals come from the bootstrap).
SELECT COUNT(*) FILTER (WHERE p)                     AS purchasing_sessions,
       COUNT(*) FILTER (WHERE p AND NOT (v AND c))   AS purchasing_sessions_outside_strict_funnel,
       COUNT(*) FILTER (WHERE p AND NOT c)           AS purchasing_sessions_without_cart,
       COUNT(*) FILTER (WHERE p AND NOT v)           AS purchasing_sessions_without_view,
       COUNT(*) FILTER (WHERE c)                     AS cart_sessions,
       COUNT(*) FILTER (WHERE c AND NOT v)           AS cart_sessions_without_view,
       COUNT(*) FILTER (WHERE c AND p)               AS cart_and_purchase_sessions,
       COUNT(*) FILTER (WHERE c AND r)               AS cart_sessions_with_remove,
       COUNT(*) FILTER (WHERE events_dedup = 1)      AS single_event_sessions
FROM q__session_flags;

-- name: order_diagnostic
-- N3: among strict-funnel converters, share whose first view <= first cart <= first purchase
-- (same-second ties count as in order).
SELECT COUNT(*)                                                                    AS view_cart_purchase_sessions,
       COUNT(*) FILTER (WHERE first_view_utc <= first_cart_utc
                          AND first_cart_utc <= first_purchase_utc)                AS in_time_order,
       ROUND(100.0 * COUNT(*) FILTER (WHERE first_view_utc <= first_cart_utc
                          AND first_cart_utc <= first_purchase_utc) / COUNT(*), 4) AS in_time_order_pct,
       COUNT(*) FILTER (WHERE first_purchase_utc < first_cart_utc)                 AS purchase_before_first_cart,
       COUNT(*) FILTER (WHERE first_cart_utc < first_view_utc)                     AS cart_before_first_view
FROM q__session_flags
WHERE v AND c AND p;

-- name: _user_flags
-- User level (F2): all events, including rows without a session.
SELECT user_id,
       bool_or(event_type = 'view')                         AS uv,
       bool_or(event_type = 'cart')                         AS uc,
       bool_or(event_type = 'purchase' AND is_valid_price)  AS up
FROM events
GROUP BY user_id;

-- name: user_stage_reach
SELECT COUNT(*)                                             AS visitors,
       COUNT(*) FILTER (WHERE uv)                           AS users_with_view,
       COUNT(*) FILTER (WHERE uc)                           AS users_with_cart,
       COUNT(*) FILTER (WHERE up)                           AS buyers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE up) / COUNT(*), 4) AS user_conversion_pct
FROM q__user_flags;

-- name: user_strict_funnel
WITH f AS (
    SELECT COUNT(*) FILTER (WHERE uv)                AS s1,
           COUNT(*) FILTER (WHERE uv AND uc)         AS s2,
           COUNT(*) FILTER (WHERE uv AND uc AND up)  AS s3
    FROM q__user_flags
)
SELECT * FROM (
    SELECT 1 AS stage_order, 'view' AS stage, s1 AS users, NULL::DOUBLE AS conversion_from_previous_pct,
           NULL::DOUBLE AS dropoff_from_previous_pct, 100.0 AS conversion_from_view_pct FROM f
    UNION ALL
    SELECT 2, 'view + cart', s2, ROUND(100.0 * s2 / s1, 4), ROUND(100.0 - 100.0 * s2 / s1, 4),
           ROUND(100.0 * s2 / s1, 4) FROM f
    UNION ALL
    SELECT 3, 'view + cart + valid purchase', s3, ROUND(100.0 * s3 / s2, 4), ROUND(100.0 - 100.0 * s3 / s2, 4),
           ROUND(100.0 * s3 / s1, 4) FROM f
) ORDER BY stage_order;

-- name: user_paths
SELECT uv AS has_view, uc AS has_cart, up AS has_valid_purchase,
       COUNT(*)                                        AS users,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS share_pct
FROM q__user_flags
GROUP BY ALL
ORDER BY has_view DESC, has_cart DESC, has_valid_purchase DESC;

-- name: _user_recovery
-- Cross-session recovery (decision R), per carting user (users with >= 1 cart in a session).
--   unordered: a cart in session s1 and a valid purchase in a DIFFERENT session s2
--   ordered:   additionally the purchase happens at or after the cart
--              (exists pair <=> first cart time in s1 <= last valid purchase time in s2)
-- Each is computed TWO independent ways (A: set logic / nearest-cart logic, B: explicit
-- join of cart sessions with purchase sessions on user_id and different session_key).
WITH cs AS (SELECT user_id, session_key, first_cart_utc FROM q__session_flags WHERE c),
ps AS (SELECT user_id, session_key, last_purchase_utc FROM q__session_flags WHERE p),
agg AS (
    SELECT user_id,
           COUNT(*) FILTER (WHERE c)          AS n_cart_sessions,
           COUNT(*) FILTER (WHERE p)          AS n_purchase_sessions,
           COUNT(*) FILTER (WHERE c AND p)    AS n_cart_and_purchase_sessions,
           MIN(session_key) FILTER (WHERE c)  AS a_cart_key,
           MIN(session_key) FILTER (WHERE p)  AS a_purchase_key
    FROM q__session_flags GROUP BY user_id HAVING bool_or(c)
),
-- A (ordered): for each purchase session, earliest cart among the user's OTHER cart sessions
ranked AS (
    SELECT user_id, session_key, first_cart_utc,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY first_cart_utc, session_key) AS rn
    FROM cs
),
e AS (
    SELECT user_id,
           MAX(session_key)    FILTER (WHERE rn = 1) AS k1,
           MAX(first_cart_utc) FILTER (WHERE rn = 1) AS t1,
           MAX(first_cart_utc) FILTER (WHERE rn = 2) AS t2
    FROM ranked WHERE rn <= 2 GROUP BY user_id
),
ord_a AS (
    SELECT DISTINCT ps.user_id
    FROM ps JOIN e USING (user_id)
    WHERE CASE WHEN ps.session_key <> e.k1 THEN e.t1 ELSE e.t2 END <= ps.last_purchase_utc
),
-- B: explicit pairs
unord_b AS (SELECT DISTINCT cs.user_id FROM cs JOIN ps ON cs.user_id = ps.user_id
            AND cs.session_key <> ps.session_key),
ord_b AS (SELECT DISTINCT cs.user_id FROM cs JOIN ps ON cs.user_id = ps.user_id
          AND cs.session_key <> ps.session_key AND cs.first_cart_utc <= ps.last_purchase_utc)
SELECT a.user_id,
       a.n_cart_sessions, a.n_purchase_sessions,
       a.n_purchase_sessions > 0                                         AS carting_buyer,
       a.n_cart_and_purchase_sessions > 0                                AS within_session_buyer,
       (a.n_purchase_sessions > 0 AND NOT (a.n_cart_sessions = 1 AND a.n_purchase_sessions = 1
                                           AND a.a_cart_key = a.a_purchase_key)) AS unordered_a,
       ub.user_id IS NOT NULL                                            AS unordered_b,
       oa.user_id IS NOT NULL                                            AS ordered_a,
       ob.user_id IS NOT NULL                                            AS ordered_b
FROM agg a
LEFT JOIN unord_b ub USING (user_id)
LEFT JOIN ord_a   oa USING (user_id)
LEFT JOIN ord_b   ob USING (user_id);

-- name: recovery_summary
SELECT COUNT(*)                                                            AS carting_users,
       COUNT(*) FILTER (WHERE carting_buyer)                               AS carting_buyers,
       COUNT(*) FILTER (WHERE within_session_buyer)                        AS within_session_buyers,
       COUNT(*) FILTER (WHERE unordered_a)                                 AS unordered_cross_session_buyers,
       COUNT(*) FILTER (WHERE ordered_a)                                   AS ordered_cross_session_recovered,
       COUNT(*) FILTER (WHERE within_session_buyer AND unordered_a)        AS overlap_within_and_unordered,
       COUNT(*) FILTER (WHERE unordered_a AND NOT ordered_a)               AS unordered_but_not_ordered,
       ROUND(100.0 * COUNT(*) FILTER (WHERE within_session_buyer) / COUNT(*), 4) AS within_session_rate_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE unordered_a) / COUNT(*), 4)    AS unordered_recovery_rate_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE ordered_a) / COUNT(*), 4)      AS ordered_recovery_rate_pct
FROM q__user_recovery;

-- name: monthly_session_funnel
-- N4: each session (with all its flags) belongs to the month of its first event (UTC).
-- Reconciliation with Day 4 (purchasing sessions by month of FIRST VALID PURCHASE):
--   P_first_event(m) - P_first_purchase(m) = incoming(m) - outgoing(m)
--   incoming(m): purchasing sessions with first event in m but first valid purchase in another month
--   outgoing(m): purchasing sessions with first valid purchase in m but first event in another month
WITH s AS (SELECT *, strftime(first_event_utc, '%Y-%m') AS fe_month,
                     strftime(first_purchase_utc, '%Y-%m') AS fp_month FROM q__session_flags),
months AS (SELECT DISTINCT fe_month AS month_utc FROM s),
by_fe AS (
    SELECT fe_month AS month_utc, COUNT(*) AS sessions,
           COUNT(*) FILTER (WHERE v) AS sessions_with_view, COUNT(*) FILTER (WHERE c) AS sessions_with_cart,
           COUNT(*) FILTER (WHERE p) AS purchasing_sessions,
           COUNT(*) FILTER (WHERE v AND c) AS view_cart_sessions,
           COUNT(*) FILTER (WHERE v AND c AND p) AS view_cart_purchase_sessions,
           COUNT(*) FILTER (WHERE p AND fp_month <> fe_month) AS incoming
    FROM s GROUP BY 1
),
by_fp AS (
    SELECT fp_month AS month_utc, COUNT(*) AS purchasing_sessions_by_first_purchase,
           COUNT(*) FILTER (WHERE fe_month <> fp_month) AS outgoing
    FROM s WHERE p GROUP BY 1
)
SELECT m.month_utc,
       COALESCE(f.sessions, 0) AS sessions, f.sessions_with_view, f.sessions_with_cart,
       COALESCE(f.purchasing_sessions, 0) AS purchasing_sessions,
       f.view_cart_sessions, f.view_cart_purchase_sessions,
       ROUND(100.0 * f.purchasing_sessions / NULLIF(f.sessions, 0), 4)                        AS session_conversion_pct,
       ROUND(100.0 * f.view_cart_sessions / NULLIF(f.sessions_with_view, 0), 4)              AS view_to_cart_pct,
       ROUND(100.0 * f.view_cart_purchase_sessions / NULLIF(f.view_cart_sessions, 0), 4)    AS cart_to_purchase_strict_pct,
       COALESCE(p.purchasing_sessions_by_first_purchase, 0) AS purchasing_sessions_by_first_purchase,
       COALESCE(f.incoming, 0) AS incoming, COALESCE(p.outgoing, 0) AS outgoing,
       COALESCE(f.purchasing_sessions, 0) - COALESCE(p.purchasing_sessions_by_first_purchase, 0) AS difference
FROM months m LEFT JOIN by_fe f USING (month_utc) LEFT JOIN by_fp p USING (month_utc)
ORDER BY m.month_utc;

-- name: _user_month_flags
-- N5: a user counts in every calendar month (UTC) with activity; stages within that month.
SELECT user_id, event_month AS month_utc,
       bool_or(event_type = 'view')                         AS uv,
       bool_or(event_type = 'cart')                         AS uc,
       bool_or(event_type = 'purchase' AND is_valid_price)  AS up
FROM events GROUP BY 1, 2;

-- name: monthly_user_funnel
SELECT month_utc,
       COUNT(*)                                     AS visitors,
       COUNT(*) FILTER (WHERE uv)                   AS users_with_view,
       COUNT(*) FILTER (WHERE uc)                   AS users_with_cart,
       COUNT(*) FILTER (WHERE up)                   AS buyers,
       COUNT(*) FILTER (WHERE uv AND uc)            AS users_view_cart,
       COUNT(*) FILTER (WHERE uv AND uc AND up)     AS users_view_cart_purchase,
       ROUND(100.0 * COUNT(*) FILTER (WHERE up) / COUNT(*), 4)                                        AS user_conversion_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE uv AND uc) / NULLIF(COUNT(*) FILTER (WHERE uv), 0), 4)    AS view_to_cart_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE uv AND uc AND up)
             / NULLIF(COUNT(*) FILTER (WHERE uv AND uc), 0), 4)                                       AS cart_to_purchase_strict_pct
FROM q__user_month_flags GROUP BY 1 ORDER BY 1;

-- name: sensitivity
-- N6: the same headline session rates under alternative inclusions. Nothing is removed from data.
WITH t AS (SELECT quantile_disc(events_dedup, 0.999) AS p999 FROM q__session_flags),
sc AS (
    SELECT 1 AS ord, 'all sessions' AS scenario, * FROM q__session_flags
    UNION ALL SELECT 2, 'excluding sessions over 24h', * FROM q__session_flags WHERE NOT over_24h
    UNION ALL SELECT 3, 'excluding top 0.1% sessions by events', s.* FROM q__session_flags s, t
              WHERE s.events_dedup < t.p999
)
SELECT ord, scenario, COUNT(*) AS sessions, COUNT(*) FILTER (WHERE p) AS purchasing_sessions,
       ROUND(100.0 * COUNT(*) FILTER (WHERE p) / COUNT(*), 4)                                       AS session_conversion_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE v AND c) / COUNT(*) FILTER (WHERE v), 4)                AS view_to_cart_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE v AND c AND p) / COUNT(*) FILTER (WHERE v AND c), 4)    AS cart_to_purchase_strict_pct,
       (SELECT p999 FROM t)                                                                         AS events_threshold_p999
FROM sc GROUP BY ord, scenario ORDER BY ord;

-- name: start_of_data_check
-- N6: sessions starting on the first day (2019-10-01 UTC) may have lost earlier stages.
SELECT CASE WHEN CAST(first_event_utc AS DATE) = DATE '2019-10-01' THEN 'first day (2019-10-01)'
            ELSE 'later days' END                                                     AS start_day_utc,
       COUNT(*)                                                                       AS sessions,
       ROUND(100.0 * COUNT(*) FILTER (WHERE p AND NOT c) / NULLIF(COUNT(*) FILTER (WHERE p), 0), 4) AS purchase_without_cart_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE c AND NOT v) / NULLIF(COUNT(*) FILTER (WHERE c), 0), 4) AS cart_without_view_pct
FROM q__session_flags GROUP BY 1 ORDER BY 1;

-- name: _user_index
-- Common user index for the bootstrap: every user in the data, positions 0..n-1.
SELECT user_id, CAST(ROW_NUMBER() OVER (ORDER BY user_id) - 1 AS BIGINT) AS u_pos
FROM (SELECT DISTINCT user_id FROM events);

-- name: _user_session_counts
-- Per-user session counts (bootstrap bases for session-level rates).
SELECT i.u_pos,
       COUNT(*)                               AS sessions,
       COUNT(*) FILTER (WHERE v)              AS v, COUNT(*) FILTER (WHERE c) AS c,
       COUNT(*) FILTER (WHERE p)              AS p,
       COUNT(*) FILTER (WHERE v AND c)        AS vc,
       COUNT(*) FILTER (WHERE v AND c AND p)  AS vcp,
       COUNT(*) FILTER (WHERE c AND p)        AS cp
FROM q__session_flags s JOIN q__user_index i USING (user_id)
GROUP BY i.u_pos;

-- name: _user_month_counts
-- Per-user, per-month counts: sessions by first-event month (N4) and user flags (N5).
WITH s AS (SELECT user_id, strftime(first_event_utc, '%Y-%m') AS month_utc,
                  COUNT(*) AS sessions, COUNT(*) FILTER (WHERE p) AS p
           FROM q__session_flags GROUP BY 1, 2)
SELECT i.u_pos, m.month_utc,
       COALESCE(s.sessions, 0) AS sessions, COALESCE(s.p, 0) AS p,
       1 AS visitor, CAST(m.up AS INTEGER) AS buyer
FROM q__user_month_flags m
JOIN q__user_index i USING (user_id)
LEFT JOIN s ON s.user_id = m.user_id AND s.month_utc = m.month_utc;
