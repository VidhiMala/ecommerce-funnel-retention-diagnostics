-- =============================================================================
-- 04_category_brand_performance.sql
-- Business question: Which categories and brands generate the revenue?
-- Decision D3: top 10 by est_revenue (A1); the rest grouped as 'other'.
--   Categories: event-level category_id (approved convention, C9); readable
--               category_code shown where it exists (~1.7% of rows have one).
--   Brands:     `brand` after the approved single-brand fill (C8); missing brand
--               is its own 'unknown' row and is never ranked.
-- Ties broken by the lower id / alphabetical brand, so ranking is deterministic.
-- purchasing_sessions, buyers and products_sold are distinct counts: NOT additive
--   across rows (a session can buy from several categories). est_revenue and
--   valid_purchase_events ARE additive and sum to the period totals.
-- =============================================================================

-- name: category_top10
WITH vp AS (SELECT * FROM events WHERE event_type = 'purchase' AND is_valid_price),
ranked AS (
    SELECT category_id,
           ROW_NUMBER() OVER (ORDER BY SUM(CAST(price AS DECIMAL(18,2))) DESC, category_id) AS rnk
    FROM vp GROUP BY category_id
),
grp AS (
    SELECT category_id,
           CASE WHEN rnk <= 10 THEN rnk ELSE 11 END                                 AS rank_order,
           CASE WHEN rnk <= 10 THEN CAST(category_id AS VARCHAR) ELSE 'other' END   AS category_group
    FROM ranked
),
agg AS (
    SELECT g.rank_order, g.category_group,
           CASE WHEN g.rank_order <= 10 THEN MIN(v.category_code) END   AS category_code,
           COUNT(DISTINCT v.category_id)                                AS categories_in_row,
           SUM(CAST(v.price AS DECIMAL(18,2)))                          AS est_revenue,
           COUNT(*)                                                     AS valid_purchase_events,
           COUNT(DISTINCT v.session_key)                                AS purchasing_sessions,
           COUNT(DISTINCT v.user_id)                                    AS buyers,
           COUNT(DISTINCT v.product_id)                                 AS products_sold
    FROM vp v JOIN grp g USING (category_id)
    GROUP BY 1, 2
    UNION ALL   -- keep a stable 11-row shape: 'other' row even when empty
    SELECT 11, 'other', NULL, 0, 0, 0, 0, 0, 0
    WHERE NOT EXISTS (SELECT 1 FROM grp WHERE rank_order = 11)
)
SELECT rank_order AS rank, category_group, category_code, categories_in_row,
       est_revenue,
       ROUND(100.0 * est_revenue / NULLIF(SUM(est_revenue) OVER (), 0), 4) AS revenue_share_pct,
       valid_purchase_events,
       ROUND(est_revenue / NULLIF(valid_purchase_events, 0), 4)            AS avg_price,
       purchasing_sessions, buyers, products_sold
FROM agg
ORDER BY rank_order;

-- name: brand_top10
WITH vp AS (SELECT * FROM events WHERE event_type = 'purchase' AND is_valid_price),
ranked AS (
    SELECT brand, ROW_NUMBER() OVER (ORDER BY SUM(CAST(price AS DECIMAL(18,2))) DESC, brand) AS rnk
    FROM vp WHERE brand IS NOT NULL GROUP BY brand
),
labelled AS (
    SELECT v.*,
           CASE WHEN v.brand IS NULL THEN 12 WHEN r.rnk <= 10 THEN r.rnk ELSE 11 END          AS rank_order,
           CASE WHEN v.brand IS NULL THEN 'unknown' WHEN r.rnk <= 10 THEN v.brand ELSE 'other' END AS brand_group
    FROM vp v LEFT JOIN ranked r USING (brand)
),
agg AS (
    SELECT rank_order, brand_group,
           COUNT(DISTINCT brand)                                                 AS brands_in_row,
           SUM(CAST(price AS DECIMAL(18,2)))                                     AS est_revenue,
           COALESCE(SUM(CAST(price AS DECIMAL(18,2))) FILTER (WHERE brand_filled), 0) AS est_revenue_from_filled_brand,
           COUNT(*)                                                              AS valid_purchase_events,
           COUNT(DISTINCT session_key)                                           AS purchasing_sessions,
           COUNT(DISTINCT user_id)                                               AS buyers,
           COUNT(DISTINCT product_id)                                            AS products_sold
    FROM labelled GROUP BY 1, 2
    UNION ALL SELECT 11, 'other', 0, 0, 0, 0, 0, 0, 0
    WHERE NOT EXISTS (SELECT 1 FROM labelled WHERE rank_order = 11)
    UNION ALL SELECT 12, 'unknown', 0, 0, 0, 0, 0, 0, 0
    WHERE NOT EXISTS (SELECT 1 FROM labelled WHERE rank_order = 12)
)
SELECT CASE WHEN rank_order <= 10 THEN rank_order END AS rank, brand_group, brands_in_row,
       est_revenue,
       ROUND(100.0 * est_revenue / NULLIF(SUM(est_revenue) OVER (), 0), 4) AS revenue_share_pct,
       est_revenue_from_filled_brand,
       valid_purchase_events,
       ROUND(est_revenue / NULLIF(valid_purchase_events, 0), 4)            AS avg_price,
       purchasing_sessions, buyers, products_sold
FROM agg
ORDER BY rank_order;

-- name: brand_coverage
-- Share of revenue and valid purchases with a known brand (every brand result states this).
SELECT COUNT(*)                                                                      AS valid_purchase_events,
       COUNT(*) FILTER (WHERE brand IS NOT NULL)                                     AS with_known_brand,
       ROUND(100.0 * COUNT(*) FILTER (WHERE brand IS NOT NULL) / COUNT(*), 4)        AS events_coverage_pct,
       ROUND(100.0 * SUM(CAST(price AS DECIMAL(18,2))) FILTER (WHERE brand IS NOT NULL)
             / SUM(CAST(price AS DECIMAL(18,2))), 4)                                 AS revenue_coverage_pct,
       COUNT(*) FILTER (WHERE brand_filled)                                          AS events_with_filled_brand,
       (SELECT COUNT(DISTINCT category_id) FROM events)                              AS categories_total,
       COUNT(DISTINCT category_id)                                                   AS categories_with_revenue,
       (SELECT COUNT(DISTINCT brand) FROM events WHERE brand IS NOT NULL)            AS brands_total,
       COUNT(DISTINCT brand)                                                         AS brands_with_revenue
FROM events WHERE event_type = 'purchase' AND is_valid_price;
