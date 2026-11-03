WITH
-- 담기 / 구매 중 먼저 끝나는 쪽의 마지막 시점
data_end AS (
    SELECT LEAST(
        (SELECT MAX(timestamp) FROM clean.add_to_cart),
        (SELECT MAX(timestamp) FROM clean.product_buy)
    ) AS max_timestamp
),

-- 담기 이벤트 + 같은 상품을 다음에 다시 담은 시점 
-- next_cart가 존재하는 경우 다음 담기 이후의 구매는 다음 담기의 구매로 상정 
-- (예: 1일에 담은 후 3일에 담고 5일에 구매 -> 3일 담기는 2일 뒤 구매, 1일 담기는 미구매)
/*
-- 재담기 직전(이전 담기 ~ 재담기 사이)에 무슨 일이 있었는지
-- 재담기 유형 확인 쿼리
WITH c AS (
    SELECT
        client_id, sku, cart_ts,
        LAG(cart_ts) OVER (PARTITION BY client_id, sku ORDER BY cart_ts) AS prev_cart_ts
    FROM (
        SELECT DISTINCT client_id, sku, timestamp AS cart_ts
        FROM clean.add_to_cart
    ) t
)
SELECT
    CASE
        WHEN EXISTS (
            SELECT 1 FROM clean.product_buy b
            WHERE b.client_id = c.client_id AND b.sku = c.sku
              AND b.timestamp > c.prev_cart_ts AND b.timestamp < c.cart_ts
        ) THEN '1. 구매 후 다시 담기'
        WHEN EXISTS (
            SELECT 1 FROM clean.remove_from_cart r
            WHERE r.client_id = c.client_id AND r.sku = c.sku
              AND r.timestamp > c.prev_cart_ts AND r.timestamp < c.cart_ts
        ) THEN '2. 제거 후 다시 담기'
        ELSE '3. 사이에 아무 기록 없음'
    END AS readd_type,
    COUNT(*)                               AS n_readd,
    COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS share
FROM c
WHERE prev_cart_ts IS NOT NULL
  AND cart_ts <= prev_cart_ts + INTERVAL 30 DAY   -- 데드라인 분석에 영향을 주는 30일 안 재담기만
GROUP BY 1
ORDER BY 1;
*/
cart_events AS (
    SELECT
        client_id, 
        sku,
        cart_timestamp,
        LEAD(cart_timestamp) OVER ( -- 정렬된 상태에서 다음 행의 시점 반환 (없으면 NULL)
            PARTITION BY client_id, sku ORDER BY cart_timestamp
        ) AS next_cart_timestamp
    FROM (
        SELECT DISTINCT client_id, sku, timestamp AS cart_timestamp
        FROM clean.add_to_cart
        WHERE client_id IS NOT NULL AND sku IS NOT NULL AND timestamp IS NOT NULL
    ) t
),

-- 30일까지 관찰 가능한 이벤트만 필터
available_cart AS (
    SELECT c.*
    FROM cart_events c
    CROSS JOIN data_end d
    WHERE c.cart_timestamp <= d.max_timestamp - INTERVAL '30 days'
),

-- 첫 구매 시점 반환: 장바구니 담기 후 30일 이내 + 다음 담기 전까지의 구매 중 가장 이른 것
purchase_time AS (
    SELECT
        c.client_id,
        c.sku,
        c.cart_timestamp,
        MIN(p.timestamp) AS purchase_timestamp
    FROM
        available_cart c
    LEFT JOIN clean.product_buy p
    ON p.client_id = c.client_id
    AND p.sku = c.sku
    AND p.timestamp > c.cart_timestamp -- 구매 시점이 장바구니에 담은 이후
    AND p.timestamp <= c.cart_timestamp + INTERVAL '30 days' -- 장바구니에 담은 후 30일 이내
    AND (c.next_cart_timestamp IS NULL OR p.timestamp <= c.next_cart_timestamp) -- 다음 담기가 없거나 다음 담기 이전 구매 시점
    GROUP BY 1,2,3
),

-- 장바구니에 담은 시점으로부터 첫 구매까지 걸린 시간
purchase_elapsed AS (
    SELECT 
        *, 
        DATE_DIFF('second', cart_timestamp, purchase_timestamp) / 3600.0 AS elapsed_hours
    FROM purchase_time
),

-- 데드라인 후보
deadlines AS (
    SELECT * FROM (VALUES (1), (3), (6), (12), (24), (48), (72), (120), (168), (336), (720))
    AS t(deadline_hours)
),

-- 데드라인별 집계
deadline_summary AS (
    SELECT
        d.deadline_hours,
        COUNT(*) AS total_cart_count, -- 전체 담기 수
        COUNT(e.purchase_timestamp) AS total_purchase_count, -- 30일 내 구매 수
        COUNT(*) FILTER (WHERE e.elapsed_hours <= d.deadline_hours) AS deadline_purchase_count -- 데드라인 내 구매 수
    FROM deadlines d
    CROSS JOIN purchase_elapsed e
    GROUP BY d.deadline_hours
),

-- 이전 데드라인 값 결합 (증가분 계산용)
result AS (
    SELECT
        *,
        LAG(deadline_purchase_count, 1, 0) OVER (ORDER BY deadline_hours) AS prev_purchase_count, -- 이전 행 구매 수
        LAG(deadline_hours, 1, 0) OVER (ORDER BY deadline_hours) AS prev_deadline_hours -- 이전 행 데드라인 시간
    FROM deadline_summary
)

SELECT
    deadline_hours,
    total_cart_count,
    total_purchase_count,
    deadline_purchase_count,
    
    -- 30일 내 구매 중 데드라인 내 구매한 비율 (구매가 0건인 경우 NULL 반환)
    ROUND(deadline_purchase_count * 100.0 / NULLIF(total_purchase_count, 0), 2) AS purchase_capture_rate,

    -- 이전 데드라인 대비 증가분 (데드라인간 간격이 다르므로 아래에서 기준 통일)
    ROUND((deadline_purchase_count - prev_purchase_count) * 100.0
            / NULLIF(total_purchase_count, 0), 2) AS incremental_capture_rate,
    
    -- 하루당 증가분 (데드라인간 간격을 하루로 통일)
    CASE 
        WHEN prev_deadline_hours = 0 THEN NULL -- 0~1인 경우 이전 누적 비율이 없으므로 NULL로 반환
        ELSE ROUND((deadline_purchase_count - prev_purchase_count) * 100.0
            / NULLIF(total_purchase_count, 0)
            / ((deadline_hours - prev_deadline_hours) /24.0), 3) 
    END AS incremental_capture_per_day,
    -- 전체 담기 건수 중 데드라인 내 구매한 비율 (전환율)
    ROUND(deadline_purchase_count * 100.0 / NULLIF(total_cart_count, 0), 2) AS cart_conversion_rate
FROM result
ORDER BY deadline_hours;
