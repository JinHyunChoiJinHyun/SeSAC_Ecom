CREATE SCHEMA IF NOT EXISTS analytics;

-- 1. 상품 연관 규칙
CREATE OR REPLACE TABLE analytics.product_association AS

-- 1-1) 전체 주문 수 조회 (support, lift의 분모로 사용)
WITH total_count AS (
    SELECT COUNT(*) AS total_cnt
    FROM mart.product_basket
),

-- 1-2) 해당 상품이 포함되어 있는 주문 수 조회 (예: 상품 100이 주문 2건에 있으면 100 -> 2)
sku_count AS (
    SELECT sku, COUNT(*) AS sku_cnt
    FROM mart.product_basket pb
    CROSS JOIN UNNEST(pb.products) AS basket_item(sku) -- list를 행으로 펼쳐 원래 주문 행에 결합 (sku 컬럼을 가진 임시 테이블 생성)
    GROUP BY sku
),

-- 1-3) 두 상품이 함께 포함되어 있는 주문 수 조회 (예: 상품 100과 300이 함께 든 주문이 5건이면 (100, 300) -> 5)
pair_count AS (
    SELECT item_a.sku AS sku_a, item_b.sku AS sku_b, COUNT(*) AS pair_cnt
    FROM mart.product_basket pb
    CROSS JOIN UNNEST(pb.products) AS item_a(sku)
    CROSS JOIN UNNEST(pb.products) AS item_b(sku)
    WHERE item_a.sku < item_b.sku -- (A,B) 혹은 (B,A) 중복 방지 및 자기 자신과의 쌍 제외
    GROUP BY item_a.sku, item_b.sku -- 상품 쌍별 집계
)
SELECT
    p.sku_a,
    p.sku_b,
    p.pair_cnt AS pair_count,
    a.sku_cnt AS sku_a_count,
    b.sku_cnt AS sku_b_count,
    t.total_cnt AS total_basket_count, -- 역산 및 검증에 사용
    p.pair_cnt * 1.0 / t.total_cnt AS support, -- 전체 주문에서 같이 구매한 비율
    p.pair_cnt * 1.0 / a.sku_cnt AS confidence_a_to_b, -- A를 구매한 경우 B를 함께 구매한 비율
    p.pair_cnt * 1.0 / b.sku_cnt AS confidence_b_to_a, -- B를 구매한 경우 A를 함께 구매한 비율
    (p.pair_cnt * 1.0 / t.total_cnt)
    / ((a.sku_cnt * 1.0 / t.total_cnt) * (b.sku_cnt * 1.0 / t.total_cnt)) AS lift -- 함께 구매한 비율이 우연히 함께 구매할 비율의 몇 배인지 계산
FROM pair_count p -- (A, B) 쌍을 함께 구매한 주문 수
CROSS JOIN total_count t -- 전체 주문 수
JOIN sku_count a ON p.sku_a = a.sku -- A가 들어있는 주문 수
JOIN sku_count b ON p.sku_b = b.sku -- B가 들어있는 주문 수
WHERE p.pair_cnt >= 10 -- 함께 구매한 주문 수가 10건 미만인 경우 제외
ORDER BY lift DESC;



