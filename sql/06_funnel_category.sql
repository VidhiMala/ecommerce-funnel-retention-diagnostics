-- =============================================================================
-- 06_funnel_category.sql
-- Business question: Which categories convert (cart / purchase) above or below the rest of the store?
-- Approved: F3 denominator = sessions that TOUCHED the segment (any event of a product in it);
--   stages = a cart / a VALID purchase of a product IN the segment.  F5 segments.
--   "rest" (for segment X) = sessions in the family that did NOT touch X; their stage flags
--   are "cart / valid purchase on any family segment" (i.e. on something they touched).
-- Segment rows are NOT additive (a session can touch several segments).
-- Statistical tests (user-clustered bootstrap, BH, F7) are computed in src/funnel.py from
--   the per-user base counts below.
-- =============================================================================

-- name: _category_segment_map
-- F5: top 10 categories by estimated revenue (A1), same rule as Day 4 (ties -> lower id);
-- every other category_id (including those with no revenue) -> 'other'.
WITH rev AS (
    SELECT category_id, SUM(CAST(price AS DECIMAL(18,2))) FILTER (WHERE event_type = 'purchase' AND is_valid_price) AS r
    FROM events GROUP BY category_id
),
ranked AS (SELECT category_id, ROW_NUMBER() OVER (ORDER BY r DESC, category_id) AS rnk FROM rev WHERE r > 0)
SELECT rev.category_id,
       CASE WHEN ranked.rnk <= 10 THEN CAST(rev.category_id AS VARCHAR) ELSE 'other' END AS segment,
       CASE WHEN ranked.rnk <= 10 THEN ranked.rnk ELSE 11 END                              AS segment_order,
       true                                                                                AS in_family
FROM rev LEFT JOIN ranked USING (category_id);

-- name: _category_session_segment
-- one row per (session, segment touched)
SELECT e.session_key, MIN(e.user_id) AS user_id, s.segment,
       bool_or(e.event_type = 'cart')                          AS cart_x,
       bool_or(e.event_type = 'purchase' AND e.is_valid_price) AS purchase_x
FROM events e JOIN q__category_segment_map s ON e.category_id = s.category_id
WHERE e.has_session AND s.in_family
GROUP BY e.session_key, s.segment;

-- name: _category_session_family
-- one row per session touching the family: cart / valid purchase on any family segment
SELECT session_key, MIN(user_id) AS user_id, bool_or(cart_x) AS cart_any, bool_or(purchase_x) AS purchase_any
FROM q__category_session_segment GROUP BY session_key;

-- name: category_funnel
WITH x AS (
    SELECT ss.segment,
           COUNT(*)                                          AS sessions_touched,
           COUNT(*) FILTER (WHERE ss.cart_x)                 AS sessions_cart_in_segment,
           COUNT(*) FILTER (WHERE ss.purchase_x)             AS sessions_purchase_in_segment,
           COUNT(DISTINCT ss.user_id)                        AS users_touched,
           COUNT(*) FILTER (WHERE sf.cart_any)               AS touched_with_cart_any,
           COUNT(*) FILTER (WHERE sf.purchase_any)           AS touched_with_purchase_any
    FROM q__category_session_segment ss JOIN q__category_session_family sf USING (session_key)
    GROUP BY ss.segment
),
fam AS (SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE cart_any) AS c, COUNT(*) FILTER (WHERE purchase_any) AS p
        FROM q__category_session_family),
labels AS (SELECT DISTINCT segment, segment_order FROM q__category_segment_map WHERE in_family)
SELECT l.segment_order, x.segment,
       x.sessions_touched, x.users_touched,
       x.sessions_cart_in_segment, x.sessions_purchase_in_segment,
       ROUND(100.0 * x.sessions_cart_in_segment / x.sessions_touched, 4)      AS cart_rate_pct,
       ROUND(100.0 * x.sessions_purchase_in_segment / x.sessions_touched, 4)  AS purchase_rate_pct,
       fam.n - x.sessions_touched                                             AS rest_sessions,
       ROUND(100.0 * (fam.c - x.touched_with_cart_any) / NULLIF(fam.n - x.sessions_touched, 0), 4)     AS rest_cart_rate_pct,
       ROUND(100.0 * (fam.p - x.touched_with_purchase_any) / NULLIF(fam.n - x.sessions_touched, 0), 4) AS rest_purchase_rate_pct
FROM x JOIN labels l USING (segment), fam
ORDER BY l.segment_order;

-- name: _category_user_segment_counts
-- bootstrap bases per (user, segment): X touched, XC cart in X, XP purchase in X,
-- XCa / XPa touched-X sessions with a cart / purchase on ANY family segment (for "rest")
SELECT i.u_pos, ss.segment,
       COUNT(*)                                    AS x,
       COUNT(*) FILTER (WHERE ss.cart_x)           AS xc,
       COUNT(*) FILTER (WHERE ss.purchase_x)       AS xp,
       COUNT(*) FILTER (WHERE sf.cart_any)         AS xca,
       COUNT(*) FILTER (WHERE sf.purchase_any)     AS xpa
FROM q__category_session_segment ss
JOIN q__category_session_family sf USING (session_key)
JOIN q__user_index i ON i.user_id = ss.user_id
GROUP BY 1, 2;

-- name: _category_user_family_counts
-- bootstrap bases per user for the whole family: T sessions, TC with cart, TP with purchase
SELECT i.u_pos, COUNT(*) AS t, COUNT(*) FILTER (WHERE cart_any) AS tc, COUNT(*) FILTER (WHERE purchase_any) AS tp
FROM q__category_session_family sf JOIN q__user_index i USING (user_id)
GROUP BY 1;
