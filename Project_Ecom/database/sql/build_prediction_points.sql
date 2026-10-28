CREATE SCHEMA IF NOT EXISTS modeling;

-- 장바구니 이벤트 기준 테이블
CREATE OR REPLACE TABLE modeling.cart_events AS

WITH cart_events_distinct AS (
    -- 장바구니 이벤트 중복 제거
    SELECT DISTINCT
        client_id,
        sku,
        "timestamp" AS cart_timestamp
    FROM clean.add_to_cart
    WHERE client_id IS NOT NULL
    AND sku IS NOT NULL
    AND "timestamp" IS NOT NULL
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
FROM cart_events_distinct;

-- 장바구니 이벤트별 예측 기준 시점 테이블 
-- 장바구니에 담은 후 24/48/72시간까지의 행동을 통해 7일 내 구매 여부 예측
CREATE OR REPLACE TABLE modeling.cart_prediction_points AS
WITH purchase_data_end AS (
    -- 구매 데이터의 마지막 시점 (구매 여부 판단을 위한 기준일)
    SELECT
        MAX("timestamp") AS purchase_data_end_timestamp
    FROM
        clean.product_buy
),

prediction_hour_options AS (
    -- 예측 기준 시점 후보 (담은 후 몇시간까지의 행동을 특징으로 사용할지)
    SELECT *
    FROM (VALUES (24), (48), (72)) AS hour_options(hours_after_cart) -- hour_options: 임시 표 이름, hours_after_cart: 컬럼명
)

-- 장바구니 이벤트 테이블과 예측 기준 시점 결합해 이벤트당 N행씩 생성
SELECT
    c.cart_event_id,
    c.client_id,
    c.sku,
    c.cart_timestamp,
    h.hours_after_cart,

    -- 아래 시점끼자의 행동만 특징으로 사용 (담은 시간 + N시간)
    c.cart_timestamp
    + h.hours_after_cart * INTERVAL '1 hour' -- 숫자를 시간으로 변환 (예: 24 -> 24시간)
    AS prediction_timestamp,

    -- 구매 여부 판단 마감 시점
    c.cart_timestamp + INTERVAL '7 days'
    AS purchase_deadline_timestamp
FROM modeling.cart_events c
CROSS JOIN prediction_hour_options h
CROSS JOIN purchase_data_end e

-- 장바구니에 담은 후 7일내 구매 여부 판단이 가능한 데이터만 사용
WHERE c.cart_timestamp + INTERVAL '7 days' <= e.purchase_data_end_timestamp;
