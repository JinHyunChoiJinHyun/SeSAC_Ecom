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

cart_events_with_prev AS (
    -- 동일 고객 / 동일 상품 이전 장바구니 담기 시점 조회 (첫 담기일 시 NULL)
    SELECT
        *,
        LAG(cart_timestamp) OVER(
            PARTITION BY client_id, sku
            ORDER BY cart_timestamp
        ) AS prev_cart_timestamp -- 동일 고객 / 동일 상품 중 바로 이전 행의 장바구니 담기 시점
    FROM cart_events_distinct
)
-- 장바구니 이벤트 ID 생성
SELECT
    -- 담은 시간 순으로 1부터 부여
    ROW_NUMBER() OVER(
        ORDER BY
            cart_timestamp,
            client_id,
            sku
    ) AS cart_event_id,

    client_id,
    sku,
    cart_timestamp
FROM cart_events_with_prev
-- 첫 장바구니 담기이거나 이전 장바구니 담기 후 데드라인만큼 지난 장바구니 데이터만 사용 (구매 하나가 여러 장바구니의 구매로 중복 연결되지 않도록 방지)
WHERE prev_cart_timestamp IS NULL
OR cart_timestamp - prev_cart_timestamp > INTERVAL '48 hours';

-- 장바구니 이벤트별 예측 기준 시점 테이블 
-- 장바구니에 담은 후 60분까지의 행동을 통해 48시간 내 구매 여부 예측
CREATE OR REPLACE TABLE modeling.cart_prediction_points AS
WITH params AS (
    SELECT
        60 AS minutes_after_cart, -- 행동 예측 기준 시점: 장바구니 담은 후 n분 (관찰 기간 분석으로 결정)
        48 AS deadline_hours -- 구매 데드라인: 장바구니 담은 후 n시간 (데드라인 분석으로 결정)
),

data_end AS (
    -- 모든 행동 테이블에 데이터가 있는 마지막 시점
    SELECT LEAST(
        (SELECT MAX("timestamp") FROM clean.add_to_cart),
        (SELECT MAX("timestamp") FROM clean.product_buy),
        (SELECT MAX("timestamp") FROM clean.remove_from_cart),
        (SELECT MAX("timestamp") FROM clean.page_visit),
        (SELECT MAX("timestamp") FROM clean.search_query)
    ) AS data_end_timestamp
)

-- 장바구니 이벤트 테이블과 예측 기준 시점 결합
SELECT
    c.cart_event_id,
    c.client_id,
    c.sku,
    c.cart_timestamp,
    p.minutes_after_cart,

    -- 예측 기준 시점(담은 시간 + N분)까지의 행동만 특징으로 사용
    c.cart_timestamp + to_minutes(p.minutes_after_cart) AS prediction_timestamp,

    -- 구매 여부 판단 마감 시점
    c.cart_timestamp + to_hours(p.deadline_hours) AS purchase_deadline_timestamp,

    -- 예측 가능 여부: 예측 기준 시점까지의 행동이 데이터 내에 모두 존재하는지
    c.cart_timestamp + to_minutes(p.minutes_after_cart) 
    <= e.data_end_timestamp
    AS is_prediction_ready,

    -- 학습 가능 여부: 데드라인이 지나 구매 여부 확정이 가능한지
    c.cart_timestamp + to_hours(p.deadline_hours)
    <= e.data_end_timestamp
    AS is_label_ready

FROM modeling.cart_events c
CROSS JOIN params p
CROSS JOIN data_end e;
