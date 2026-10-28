CREATE SCHEMA IF NOT EXISTS modeling;

CREATE OR REPLACE TABLE modeling.cart_events AS

-- 장바구니 이벤트 중복 제거
WITH cart_events_distinct AS (
    SELECT DISTINCT
        client_id,
        sku,
        "timestamp" AS TIMESTAMP AS cart_timestamp
    FROM raw.add_to_cart
    WHERE client_id IS NOT NULL
    AND sku IS NOT NULL
    AND "timestamp" AS TIMESTAMP IS NOT NULL
),

-- 장바구니 이벤트 ID 생성 (담은 시간 순으로 1부터 부여)
SELECT
    ROW_NUMBER() OVER(
        ORDER BY
            cart_timestamp,
            client_id,
            sku
    ) AS cart_event_id,

    client_id,
    sku,
    cart_timestamp
FROM cart_events_distinct

-- 구매 이벤트 조회
purchases AS (
    SELECT
        client_id,
        sku,
        TRY_CAST("timestamp" AS TIMESTAMP) AS buy_timestamp
    FROM raw.product_buy
    WHERE TRY_CAST("timestamp" AS TIMESTAMP) IS NOT NULL
),

-- 구매 데이터 마지막 시점 조회 (구매 여부 판단을 위한 기준일)
observation AS (
    SELECT
        MAX(buy_timestamp) AS purchase_data_end
    FROM
        purchases
)