CREATE SCHEMA IF NOT EXISTS mart;

-- 날짜 x 카테고리 x 시간대별 구매 건수 집계 테이블 생성 (규칙 기반, 시계열 분석 입력)
CREATE OR REPLACE TABLE mart.category_hourly_base AS
-- 구매 데이터에 category 결합 및 날짜 x 카테고리 x 시간대별 구매 건수 / 구매자 수 집계
WITH sku_category AS (
    SELECT DISTINCT
        sku,
        category
    FROM raw.product_properties
    WHERE sku IS NOT NULL
    AND category IS NOT NULL
)
SELECT
    CAST(pb.timestamp AS DATE) AS event_date, -- 날짜로 변환 (예: 2026-03-05)
    sc.category,
    HOUR(pb.timestamp) AS hour,
    COUNT(*) AS purchase_count,
    COUNT(DISTINCT pb.client_id) AS buyer_count
FROM
    clean.product_buy pb
JOIN sku_category sc
ON pb.sku = sc.sku
WHERE pb.timestamp IS NOT NULL
GROUP BY 1, 2, 3; -- SELECT의 1,2,3번쨰 컬럼 (날짜, 카테고리, 시간대)로 묶어서 집계