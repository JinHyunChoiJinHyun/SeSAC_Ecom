    CREATE SCHEMA IF NOT EXISTS mart;

    -- 1. 고객별 동시에 구매한 상품 묶음 (같은 고객 + 같은 구매 시각 = 한 주문)
    CREATE OR REPLACE TABLE mart.product_basket AS
    SELECT
        client_id,
        timestamp AS purchase_time,
        LIST(DISTINCT sku ORDER BY sku) AS products, -- 구매한 상품들을 중복 제거 후 정렬하여 리스트 생성 (구매 묶음)
        COUNT(DISTINCT sku) AS product_count -- 구매한 상품 종류 수
    FROM clean.product_buy
    WHERE sku IS NOT NULL
    GROUP BY
        client_id,
        timestamp;