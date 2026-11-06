-- 장바구니 담기 후 관찰 기간 결정 - 데드라인 48시간 기준
-- 우선 1시간이 가장 적합
WITH params AS (
    SELECT
        48 AS deadline_hours, -- 구매 데드라인 (담은 뒤 48시간)
        48 AS prev_cart_gap_hours -- 재담기 제거 기준 간격 (이전 담기 후 48시간 내 담기 제거)
),

-- 관찰 기간 후보
windows AS (
    SELECT * FROM (VALUES (1), (3), (6), (12), (24)) AS t(window_hours)
),

-- 테이블의 마지막 관측 시점 중 가장 이른 시점 (모든 테이블의 데이터가 존재하는 시점)
data_end AS (
    SELECT LEAST(
        (SELECT MAX(timestamp) FROM clean.add_to_cart),
        (SELECT MAX(timestamp) FROM clean.product_buy),
        (SELECT MAX(timestamp) FROM clean.remove_from_cart),
        (SELECT MAX(timestamp) FROM clean.page_visit),
        (SELECT MAX(timestamp) FROM clean.search_query)
    ) AS max_ts
),

-- 같은 초 중복 제거 + 이전 담기 후 48시간 내 재담기 제거 + 48시간까지 구매 여부 확인 가능한 담기 이벤트만 필터
cart_events AS (
    SELECT 
        ROW_NUMBER() OVER (ORDER BY a.cart_ts, a.client_id, a.sku) AS cart_id, -- 담은 시각 순서대로 id 생성
        a.client_id,
        a.sku,
        a.cart_ts
    FROM (
        SELECT
            client_id,
            sku,
            cart_ts,
            -- 동일 고객, 동일 상품의 이전 담기 시점 (없으면 NULL)
            LAG(cart_ts) OVER (PARTITION BY client_id, sku ORDER BY cart_ts) AS prev_cart_ts 
        FROM
            (
                SELECT DISTINCT -- 같은 초에 중복 기록된 담기 제거
                    client_id,
                    sku,
                    timestamp AS cart_ts
                FROM
                    clean.add_to_cart
                -- cart_base와 기준 통일
                WHERE client_id IS NOT NULL
                AND sku IS NOT NULL
                AND timestamp IS NOT NULL 
            ) x
    ) a
    CROSS JOIN params p
    CROSS JOIN data_end de
    -- 이전 담기가 없거나 이전 담기와의 차이가 gap보다 큰 데이터만 반환
    WHERE (a.prev_cart_ts IS NULL OR a.cart_ts - a.prev_cart_ts > to_hours(p.prev_cart_gap_hours))
    AND a.cart_ts + to_hours(p.deadline_hours) <= de.max_ts -- 담기 후 48시간이 데이터 끝을 넘지 않는 데이터만 반환
),

-- 담기별 48시간 내 장바구니 담은 상품의 첫 구매 시각
first_buy AS (
    SELECT c.cart_id, MIN(b.timestamp) AS first_buy_ts
    FROM cart_events c
    CROSS JOIN params p
    JOIN clean.product_buy b
    ON b.client_id = c.client_id
    AND b.sku = c.sku
    AND b.timestamp >= c.cart_ts -- 구매 시각이 담은 시간 이후인 경우
    AND b.timestamp <= c.cart_ts + to_hours(p.deadline_hours) -- 구매 시각이 담기 후 데드라인 이내인 경우
    GROUP BY c.cart_id
),

-- 행동 이벤트 통합
behavior_events AS (
    -- 고객 id, 시각, 행동 이벤트 명, 제품 번호
    SELECT client_id, timestamp AS ts, 'add_to_cart' AS event_type, sku FROM clean.add_to_cart
    UNION ALL
    SELECT client_id, timestamp AS ts, 'remove_from_cart' AS event_type, sku FROM clean.remove_from_cart
    UNION ALL
    SELECT client_id, timestamp AS ts, 'page_visit' AS event_type, NULL FROM clean.page_visit
    UNION ALL
    SELECT client_id, timestamp AS ts, 'search_query' AS event_type, NULL FROM clean.search_query
),

-- 최대 관찰 기간으로 결합 후 담기 후 시간별로 묶어서 집계
cart_hourly_behavior AS (
    SELECT
        c.cart_id,
        CASE
            -- remove의 경우 담은 상품 제거인지 다른 상품 제거인지 분기
            WHEN b.event_type = 'remove_from_cart' AND b.sku = c.sku THEN 'remove_target'
            WHEN b.event_type = 'remove_from_cart' THEN 'remove_other'
            ELSE b.event_type -- remove를 제외한 행동 이벤트 반환
        END AS event_type,
        -- 각 행동 이벤트를 담은 후 경과 시간 기준 1시간 단위 칸으로 분류 (예: 30분 경과 -> 1번 칸, 1시간 15분 경과 -> 2번 칸)
        CAST(CEIL(DATE_DIFF('second', c.cart_ts, b.ts) / 3600.0) AS INTEGER) AS hour_num,
        COUNT(*) AS cnt
    FROM cart_events c
    JOIN behavior_events b
    ON b.client_id = c.client_id
    AND b.ts > c.cart_ts -- 행동이 담기 이후인 경우
    AND b.ts <= c.cart_ts + to_hours((SELECT MAX(window_hours) FROM windows)) -- 행동이 담기 후 관찰 기간 내인 경우
    GROUP BY 1, 2, 3
),

-- 담기 x 관찰 기간별 누적 행동 + 구매 상태
cart_window AS (
    SELECT
        c.cart_id,
        w.window_hours,
        fb.first_buy_ts IS NOT NULL AS bought_by_deadline,
        COALESCE(fb.first_buy_ts <= c.cart_ts + to_hours(w.window_hours), FALSE) AS bought_in_window,
        COALESCE(SUM(hb.cnt) FILTER (WHERE hb.event_type = 'page_visit'), 0) AS visit_cnt,
        COALESCE(SUM(hb.cnt) FILTER (WHERE hb.event_type = 'search_query'), 0) AS search_cnt,
        COALESCE(SUM(hb.cnt) FILTER (WHERE hb.event_type = 'add_to_cart'), 0) AS add_cnt,
        COALESCE(SUM(hb.cnt) FILTER (WHERE hb.event_type = 'remove_target'), 0) AS remove_target_cnt,
        COALESCE(SUM(hb.cnt) FILTER (WHERE hb.event_type = 'remove_other'), 0) AS remove_other_cnt
    FROM cart_events c
    CROSS JOIN windows w
    LEFT JOIN first_buy fb ON fb.cart_id = c.cart_id
    -- 관찰 기간 이하의 칸을 전부 가져와 누적 (예: 관찰 기간이 6시간인 경우 -> 1~6번 칸의 행동 수의 합계)
    LEFT JOIN cart_hourly_behavior hb ON hb.cart_id = c.cart_id AND hb.hour_num <= w.window_hours
    GROUP BY c.cart_id, c.cart_ts, w.window_hours, fb.first_buy_ts
)

SELECT
    w.window_hours,
    (SELECT deadline_hours FROM params) - w.window_hours AS hours_left_for_promotion,
    COUNT(*) AS cart_cnt,

    -- 관찰 기간 이내 구매 시 예측 및 프로모션 대상에서 제외
    ROUND(AVG(bought_in_window::INT) * 100, 2) AS bought_in_window_pct_all, -- 전체 장바구니 중 관찰 기간 내 구매한 비율 (높을수록 프로모션 대상자 감소)
    ROUND(AVG(bought_in_window::INT) FILTER (WHERE bought_by_deadline) * 100, 2) AS bought_in_window_pct_buy, -- 데드라인 내 구매한 장바구니 중 관찰 기간 내 구매한 비율 (높을수록 행동 데이터 손실 증가)

    -- 관찰 기간 종료 시점의 예측 대상 및 구매 전환 현황
    COUNT(*) FILTER (WHERE NOT bought_in_window) AS prediction_target_cnt, -- 예측 대상으로 남아있는 장바구니 수 (높을수록 구매/미구매 패턴 학습 가능한 전체 데이터 확보에 유리)
    COUNT(*) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) AS prediction_positive_cnt, -- 예측 대상 중 데드라인 내 실제 구매한 장바구니 수 (높을수록 구매 패턴 학습에 필요한 데이터 확보에 유리)
    ROUND(AVG(bought_by_deadline::INT) FILTER (WHERE NOT bought_in_window) * 100, 2) AS prediction_conversion_rate, -- 예측 대상 중 실제 구매한 비율 (구매/미구매 데이터 비율 확인)

    -- 예측 대상 내 구매(_b) / 미구매(_n) 행동 차이 (관찰 기간을 늘려도 서로의 차이가 벌어지지 않으면 기다릴 이유가 감소)
    ROUND(AVG((visit_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) * 100, 2) as visit_rate_b,
    ROUND(AVG((visit_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) * 100, 2) as visit_rate_n,
    ROUND(AVG((search_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) * 100, 2) as search_rate_b,
    ROUND(AVG((search_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) * 100, 2) as search_rate_n,
    ROUND(AVG((add_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) * 100, 2) as add_rate_b,
    ROUND(AVG((add_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) * 100, 2) as add_rate_n,
    ROUND(AVG((remove_target_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) * 100, 2) as remove_target_rate_b,
    ROUND(AVG((remove_target_cnt > 0)::INT) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) * 100, 2) as remove_target_rate_n,

    MEDIAN(visit_cnt) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) AS median_visit_b,
    MEDIAN(visit_cnt) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) AS median_visit_n,
    MEDIAN(search_cnt) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) AS median_search_b,
    MEDIAN(search_cnt) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) AS median_search_n,
    MEDIAN(add_cnt) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) AS median_add_b,
    MEDIAN(add_cnt) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) AS median_add_n,
    MEDIAN(remove_target_cnt) FILTER (WHERE NOT bought_in_window AND bought_by_deadline) AS median_remove_b,
    MEDIAN(remove_target_cnt) FILTER (WHERE NOT bought_in_window AND NOT bought_by_deadline) AS median_remove_n
FROM cart_window w
GROUP BY w.window_hours
ORDER BY w.window_hours;
