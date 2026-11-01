-- 예측 기준 시점별 특징 테이블
CREATE OR REPLACE TABLE modeling.features AS
SELECT 
    p.cart_event_id,
    p.hours_after_cart,
    p.client_id,
    p.sku,
    p.cart_timestamp,

    -- 담기 전 7일간 페이지 방문 횟수
    (
        SELECT COUNT(*)
        FROM clean.page_visit pv
        WHERE pv.client_id = p.client_id
        AND pv."timestamp" >= p.cart_timestamp - INTERVAL '7 days'
        AND pv."timestamp" < p.cart_timestamp
    ) AS page_visit_count_7d_before,

    -- 담기 전 해당 상품 구매 횟수
    (
        SELECT COUNT(*)
        FROM clean.product_buy b
        WHERE b.client_id = p.client_id
        AND b.sku = p.sku
        AND b."timestamp" < p.cart_timestamp
    ) AS past_sku_buy_count,

    -- 담은 후 예측 시점까지 페이지 방문 횟수
    (
        SELECT COUNT(*)
        FROM clean.page_visit pv
        WHERE pv.client_id = p.client_id
        AND pv."timestamp" > p.cart_timestamp
        AND pv."timestamp" <= p.prediction_timestamp
    ) AS page_visit_count_after_cart,

    -- 담은 후 예측 시점까지 검색 횟수
    (
        SELECT COUNT(*)
        FROM clean.search_query sq
        WHERE sq.client_id = p.client_id
        AND sq."timestamp" > p.cart_timestamp
        AND sq."timestamp" <= p.prediction_timestamp
    ) AS search_count_after_cart,

    -- 담은 후 같은 상품을 장바구니에서 제거한 횟수 (장바구니 이탈 데이터)
    (
        SELECT COUNT(*)
        FROM clean.remove_from_cart r
        WHERE r.client_id = p.client_id
        AND r.sku = p.sku
        AND r."timestamp" > p.cart_timestamp
        AND r."timestamp" <= p.prediction_timestamp
    ) AS same_sku_remove_count_after_cart,

    -- 담은 후 추가로 장바구니에 담은 횟수
    (
        SELECT COUNT(*)
        FROM clean.add_to_cart a
        WHERE a.client_id = p.client_id
        AND a."timestamp" > p.cart_timestamp
        AND a."timestamp" <= p.prediction_timestamp
    ) AS cart_add_count_after_cart,

    -- 상품 정보 (SKU당 1행이라 집계 데이터가 아니므로 JOIN)
    pp.category AS product_category,
    pp.price AS product_price

FROM modeling.cart_prediction_points p

LEFT JOIN raw.product_properties pp
ON p.sku = pp.sku;
