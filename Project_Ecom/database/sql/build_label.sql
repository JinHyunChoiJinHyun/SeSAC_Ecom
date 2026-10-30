-- 예측 기준 시점별 구매 라벨 테이블
CREATE OR REPLACE TABLE modeling.labels AS
SELECT
    p.cart_event_id,
    p.hours_after_cart,

    CASE
        WHEN NOT p.is_label_ready THEN NULL -- 구매 여부 확정 불가 시 NULL로 표기
        WHEN EXISTS (
            SELECT 1
            FROM clean.product_buy b
            WHERE b.client_id = p.client_id
            AND b.sku = p.sku
            AND b."timestamp" > p.prediction_timestamp -- 예측 기준 시점 이후 구매
            AND b."timestamp" <= p.purchase_deadline_timestamp -- 예측 기간 내 구매
        )
        THEN 1
        ELSE 0
    END AS label
FROM modeling.cart_prediction_points p

-- 예측 기준 시점까지의 행동이 쌓여 예측 가능한 이벤트 데이터만 사용
WHERE p.is_prediction_ready

 -- 기준 시점 이전에 구매한 이벤트는 제외 (구매 후 행동이 특징에 섞이므로 기준 시점에 아직 구매하지 않은 장바구니 이벤트만 학습 필요)
AND NOT EXISTS (
    SELECT 1
    FROM clean.product_buy b
    WHERE b.client_id = p.client_id
    AND b.sku = p.sku
    AND b."timestamp" > p.cart_timestamp -- 장바구니에 담은 이후
    AND b."timestamp" <= p.prediction_timestamp -- 예측 기준 시점 이전 구매
);
