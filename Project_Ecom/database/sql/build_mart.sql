-- MART 스키마 생성
CREATE SCHEMA IF NOT EXISTS mart;
-- 구매 예측 학습 데이터 테이블 생성
CREATE OR REPLACE TABLE mart.purchase_training_data AS
SELECT
    f.*,
    l.label
FROM modeling.features f
INNER JOIN modeling.labels l
ON f.cart_event_id = l.cart_event_id
AND f.hours_after_cart = l.hours_after_cart;