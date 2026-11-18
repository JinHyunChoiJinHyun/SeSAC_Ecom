========================================
[amazon schema — Amazon 외부 리뷰 데이터]
========================================

Amazon Reviews 2023, Clothing·Shoes·Jewelry. 리뷰 기간 1998 ~ 2023-09.

판매량 정보가 없습니다. 리뷰 수·별점을 판매량으로 표현하지 마십시오.
H&M과 공통 ID가 없습니다.

----------------------------------------
1. amazon.meta  (7,218,481행)
----------------------------------------
한 행: 상품 그룹 1개 (색·사이즈 옵션 묶음)

- parent_asin      VARCHAR    PK
- main_category    VARCHAR    대분류 (NULL 12.9%)
- title            VARCHAR    상품명 (영문)
- average_rating   FLOAT      평균 별점
- rating_number    BIGINT     평가 수
- features         VARCHAR[]  특징 문장 목록
- description      VARCHAR[]  설명 문장 목록 (47.5%가 빈 목록)
- price            VARCHAR    가격 문자열 (유효 값 약 20%). 숫자 비교는 TRY_CAST(price AS DOUBLE)
- store            VARCHAR    브랜드/스토어
- categories       VARCHAR[]  카테고리 경로 목록
                              예: ['Clothing, Shoes & Jewelry', 'Women', 'Clothing', 'Sweaters', 'Pullovers']
- details          JSON       소재·치수 등 상세 정보
- images, videos   JSON       이미지·영상 정보
- bought_together  VARCHAR[]

----------------------------------------
2. amazon.review  (66,033,346행)
----------------------------------------
한 행: 리뷰 1건

- parent_asin        VARCHAR   → amazon.meta.parent_asin (N:1)
- asin               VARCHAR   옵션별 상품 ID
- user_id            VARCHAR   작성자 ID (결과에 노출하지 말 것)
- rating             DECIMAL   별점 1~5
- title              VARCHAR   리뷰 제목 (영문)
- text               VARCHAR   리뷰 본문 (영문)
- images             STRUCT[]  리뷰 사진
- timestamp          BIGINT    작성 시각 (밀리초 epoch)
                               → make_timestamp(timestamp * 1000) 또는 to_timestamp(timestamp / 1000)
- helpful_vote       INTEGER   도움 투표 수 (일부 음수 이상값 있음)
- verified_purchase  BOOLEAN   구매 인증 여부

Amazon 사용 규칙
- 상품 범위는 categories로 거르십시오. 의류만 볼 때 신발·스포츠 브랜드가 섞이므로 필수입니다.
  예: list_contains(categories, 'Women') AND list_has_any(categories, ['Sweaters', 'Cardigans'])
- 상품명 키워드는 lower(title) LIKE '%turtleneck%' 처럼 소문자로 비교하십시오.
- 평가 수가 적은 상품은 근거가 약하므로, 상품 단위 비교에는 rating_number >= 50 같은 하한을 두십시오.
- 리뷰 본문 키워드 비율은 COUNT(*) FILTER (WHERE regexp_matches(lower(text), '...')) 처럼 집계하십시오.
- 리뷰 원문 text 전체를 대량으로 반환하지 마십시오.
  예시 문장이 필요하면 LEFT(text, 200)으로 자르고 LIMIT 20 이하로 제한하십시오.
- user_id는 최종 SELECT에 포함하지 마십시오.
- H&M과 같은 시기를 볼 때는 리뷰 연도 2018~2020으로 제한할 수 있습니다.

Amazon 대용량 규칙
- amazon.review(약 6,603만 행)는 반드시 amazon.meta와 JOIN한 categories 조건이나
  parent_asin 조건으로 범위를 좁힌 뒤 집계하십시오.
- 원본 리뷰 행을 그대로 반환하지 마십시오.
- 집계 alias 예: review_count, avg_rating, negative_review_ratio

Amazon SQL 예시 (참고용, 그대로 재사용하지 말 것)

-- Amazon 여성 스웨터·카디건 리뷰의 별점 분포 (2018~2020)
SELECT r.rating, COUNT(*) AS review_count
FROM amazon.review r
JOIN amazon.meta m ON r.parent_asin = m.parent_asin
WHERE list_contains(m.categories, 'Women')
  AND list_has_any(m.categories, ['Sweaters', 'Cardigans'])
  AND year(make_timestamp(r.timestamp * 1000)) BETWEEN 2018 AND 2020
GROUP BY r.rating
ORDER BY r.rating;

-- Amazon 리뷰 키워드 언급률 (좋은 리뷰 vs 나쁜 리뷰)
WITH k AS (
    SELECT r.rating, lower(coalesce(r.title, '') || ' ' || coalesce(r.text, '')) AS t
    FROM amazon.review r
    JOIN amazon.meta m ON r.parent_asin = m.parent_asin
    WHERE list_contains(m.categories, 'Women')
      AND list_has_any(m.categories, ['Sweaters'])
      AND r.verified_purchase
)
SELECT
    CASE WHEN rating >= 4 THEN '4~5점' WHEN rating <= 2 THEN '1~2점' ELSE '3점' END AS rating_group,
    COUNT(*) AS review_count,
    ROUND(100.0 * AVG(regexp_matches(t, 'itch|scratch')::INT), 2) AS itchy_pct,
    ROUND(100.0 * AVG(regexp_matches(t, 'runs small|too small|size up')::INT), 2) AS runs_small_pct
FROM k
GROUP BY 1
ORDER BY 1;
