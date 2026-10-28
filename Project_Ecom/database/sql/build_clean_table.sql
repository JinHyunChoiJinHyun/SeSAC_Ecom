-- 원본 테이블의 timestamp를 문자열에서 TIMESTAMP 타입으로 변환
CREATE SCHEMA IF NOT EXISTS clean;

CREATE OR REPLACE TABLE clean.add_to_cart AS
SELECT * REPLACE (TRY_CAST("timestamp" AS TIMESTAMP) AS "timestamp") 
FROM raw.add_to_cart
WHERE TRY_CAST("timestamp" AS TIMESTAMP) IS NOT NULL;