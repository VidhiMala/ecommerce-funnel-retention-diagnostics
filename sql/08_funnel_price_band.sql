-- =============================================================================
-- 08_funnel_price_band.sql
-- Business question: Does conversion change with product price level?
-- Approved: F3 denominator = sessions that TOUCHED the segment (any event of a product in it);
--   stages = a cart / a VALID purchase of a product IN the segment.  F5 segments.
--   "rest" (for segment X) = sessions in the family that did NOT touch X; their stage flags
--   are "cart / valid purchase on any family segment" (i.e. on something they touched).
-- Segment rows are NOT additive (a session can touch several segments).
-- Statistical tests (user-clustered bootstrap, BH, F7) are computed in src/funnel.py from
--   the per-user base counts below.
-- =============================================================================

-- Price bands (approved correction, 2026-11-27):
--   1. each product's MEDIAN POSITIVE PRICE over its deduplicated events (all event types);
--   2. quintile boundaries (20/40/60/80th percentiles) ACROSS PRODUCTS, one weight per product;
--   3. band k if boundary(k-1) < median <= boundary(k); band 1 includes the minimum, band 5 the maximum,
--      so products with identical medians always share a band;
--   4. products with no positive price -> 'no valid price': reported, never banded, excluded from tests.
--   Events inherit their product's band (a price change does not move a product between bands).

-- name: _product_price
SELECT product_id, median(price) FILTER (WHERE price > 0) AS median_price
FROM events GROUP BY product_id;

-- name: price_band_boundaries
WITH q AS (SELECT quantile_cont(median_price, [0.2, 0.4, 0.6, 0.8]) AS b
           FROM q__product_price WHERE median_price IS NOT NULL),
mm AS (SELECT MIN(median_price) AS lo, MAX(median_price) AS hi FROM q__product_price)
SELECT k AS band, 'band_' || k AS segment,
       CASE WHEN k = 1 THEN NULL ELSE q.b[k - 1] END     AS lower_exclusive,
       CASE WHEN k = 5 THEN mm.hi ELSE q.b[k] END        AS upper_inclusive,
       CASE WHEN k = 1 THEN mm.lo END                    AS minimum_median
FROM range(1, 6) r(k), q, mm
ORDER BY k;

-- name: _product_band
SELECT p.product_id, p.median_price,
       CASE WHEN p.median_price IS NULL THEN NULL
            WHEN p.median_price <= b.b[1] THEN 1 WHEN p.median_price <= b.b[2] THEN 2
            WHEN p.median_price <= b.b[3] THEN 3 WHEN p.median_price <= b.b[4] THEN 4 ELSE 5 END AS band
FROM q__product_price p,
     (SELECT quantile_cont(median_price, [0.2, 0.4, 0.6, 0.8]) AS b FROM q__product_price
      WHERE median_price IS NOT NULL) b;

-- name: price_band_products
SELECT COALESCE('band_' || band, 'no valid price') AS segment, COALESCE(band, 6) AS segment_order,
       COUNT(*) AS products, MIN(median_price) AS min_median_price, MAX(median_price) AS max_median_price
FROM q__product_band GROUP BY 1, 2 ORDER BY 2;

-- name: _price_band_segment_map
SELECT product_id, COALESCE('band_' || band, 'no valid price') AS segment, COALESCE(band, 6) AS segment_order,
       band IS NOT NULL AS in_family
FROM q__product_band;

-- name: price_band_no_valid_price_sessions
-- Sessions touching products without a positive price (reported separately, not banded).
SELECT COUNT(DISTINCT e.session_key) AS sessions_touching_no_valid_price_products,
       COUNT(DISTINCT e.product_id)  AS products_touched
FROM events e JOIN q__product_band b USING (product_id)
WHERE e.has_session AND b.band IS NULL;

-- name: _price_band_session_segment
-- one row per (session, segment touched)
SELECT e.session_key, MIN(e.user_id) AS user_id, s.segment,
       bool_or(e.event_type = 'cart')                          AS cart_x,
       bool_or(e.event_type = 'purchase' AND e.is_valid_price) AS purchase_x
FROM events e JOIN q__price_band_segment_map s ON e.product_id = s.product_id
WHERE e.has_session AND s.in_family
GROUP BY e.session_key, s.segment;

-- name: _price_band_session_family
-- one row per session touching the family: cart / valid purchase on any family segment
SELECT session_key, MIN(user_id) AS user_id, bool_or(cart_x) AS cart_any, bool_or(purchase_x) AS purchase_any
FROM q__price_band_session_segment GROUP BY session_key;

-- name: price_band_funnel
WITH x AS (
    SELECT ss.segment,
           COUNT(*)                                          AS sessions_touched,
           COUNT(*) FILTER (WHERE ss.cart_x)                 AS sessions_cart_in_segment,
           COUNT(*) FILTER (WHERE ss.purchase_x)             AS sessions_purchase_in_segment,
           COUNT(DISTINCT ss.user_id)                        AS users_touched,
           COUNT(*) FILTER (WHERE sf.cart_any)               AS touched_with_cart_any,
           COUNT(*) FILTER (WHERE sf.purchase_any)           AS touched_with_purchase_any
    FROM q__price_band_session_segment ss JOIN q__price_band_session_family sf USING (session_key)
    GROUP BY ss.segment
),
fam AS (SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE cart_any) AS c, COUNT(*) FILTER (WHERE purchase_any) AS p
        FROM q__price_band_session_family),
labels AS (SELECT DISTINCT segment, segment_order FROM q__price_band_segment_map WHERE in_family)
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

-- name: _price_band_user_segment_counts
-- bootstrap bases per (user, segment): X touched, XC cart in X, XP purchase in X,
-- XCa / XPa touched-X sessions with a cart / purchase on ANY family segment (for "rest")
SELECT i.u_pos, ss.segment,
       COUNT(*)                                    AS x,
       COUNT(*) FILTER (WHERE ss.cart_x)           AS xc,
       COUNT(*) FILTER (WHERE ss.purchase_x)       AS xp,
       COUNT(*) FILTER (WHERE sf.cart_any)         AS xca,
       COUNT(*) FILTER (WHERE sf.purchase_any)     AS xpa
FROM q__price_band_session_segment ss
JOIN q__price_band_session_family sf USING (session_key)
JOIN q__user_index i ON i.user_id = ss.user_id
GROUP BY 1, 2;

-- name: _price_band_user_family_counts
-- bootstrap bases per user for the whole family: T sessions, TC with cart, TP with purchase
SELECT i.u_pos, COUNT(*) AS t, COUNT(*) FILTER (WHERE cart_any) AS tc, COUNT(*) FILTER (WHERE purchase_any) AS tp
FROM q__price_band_session_family sf JOIN q__user_index i USING (user_id)
GROUP BY 1;
