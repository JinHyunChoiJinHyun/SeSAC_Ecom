-- 시계열 분석 모델 사용 (추후 성능이 안나오면 lightGbm으로 대체 -> 특징 테이블 필요 혹은 규칙 기반으로 결정)
/*
모델	특징	추천 용도
Seasonal Naive	과거 동일 요일·시간대 활용	Baseline
Prophet	추세와 계절성 반영	비교 모델
XGBoost / LightGBM	시간, 요일, 과거 구매 건수 등 다양한 변수 활용	추천
LSTM	장기적인 시계열 패턴 학습	고도화
*/
CREATE SCHEMA IF NOT EXISTS mart;

-- 구매 건수가 존재하지 않아 행이 없는 데이터를 구매 건수 0으로 채움
CREATE OR REPLACE TABLE mart.category_hourly_timeseries AS
WITH params AS (
    SELECT 2000 AS min_purchase -- 대상 카테고리의 최소 구매 건수 (check 4번 확인 후 조정)
),

-- 최소 구매 건수 이상 카테고리만 반환 (구매가 적으면 학습이 어렵고 불필요한 격자 크기 증가 초래)
target_category AS (
    SELECT category
    FROM mart.category_hourly_base
    GROUP BY category
    HAVING SUM(purchase_count) >= (SELECT min_purchase FROM params)
),

-- 첫날부터 마지막 날까지 모든 날짜 목록 생성 (추후 구매 건수가 존재하지 않아 행이 없는 데이터에 날짜 행 생성을 위해 필요)
date_range AS (
    SELECT CAST(UNNEST(GENERATE_SERIES( -- 시작부터 끝까지 interval만큼 더한 값을 리스트로 생성 후 행으로 펼쳐 결합에 사용
        (SELECT MIN(event_date) FROM mart.category_hourly_base)::TIMESTAMP, -- 첫날 (예: 2026-03-01) -> timestamp로 형 변환
        (SELECT MAX(event_date) FROM mart.category_hourly_base)::TIMESTAMP, -- 마지막 날 (예: 2026-08-31) -> timestamp로 형 변환
        INTERVAL 1 DAY -- 더할 날짜
    )) AS DATE) AS event_date
),

-- 0 ~ 23시 생성
hours AS (
    SELECT UNNEST(RANGE(24)) AS hour
),

-- 대상 카테고리 x 날짜 x 시간 조합 격자 생성
time_grid AS (
    SELECT
        c.category,
        d.event_date,
        h.hour
    FROM target_category c
    CROSS JOIN date_range d
    CROSS JOIN hours h
)

-- 생성한 격자에 구매 건수 결합
SELECT
    g.category,
    g.event_date,
    g.hour,
    g.event_date + to_hours(g.hour) AS ts, -- 시계열 인덱스로 사용 (예: 2026-03-01 21:00)
    COALESCE(b.purchase_count, 0) AS purchase_count -- 구매가 없는 시간대는 0으로 대체
FROM time_grid g
LEFT JOIN mart.category_hourly_base b -- 격자는 남기고 짝이 없는 경우 NULL
ON b.category = g.category
AND b.event_date = g.event_date
AND b.hour = g.hour;