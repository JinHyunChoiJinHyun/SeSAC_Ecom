========================================
[hm schema — H&M 자사 데이터]
========================================

Kaggle H&M Personalized Fashion Recommendations. 기간 2018-09-20 ~ 2020-09-22.
사용자가 이 기간 밖을 요청하면 있는 범위로만 조회하고 scope_note에 적으십시오.

----------------------------------------
1. hm.transactions  (31,788,324행)
----------------------------------------
한 행: 구매 기록 1건 (주문 ID 없음, 수량 컬럼 없음)

- t_dat             DATE      구매일 (시각 정보 없음)
- customer_id       VARCHAR   고객 ID (해시 문자열)
- article_id        VARCHAR   상품(색상별 변형) ID, 10자리 문자열 (앞자리 0 포함)
- price             DOUBLE    정규화된 가격 지표 (0.00002 ~ 0.59). 원화·유로 금액이 아님
- sales_channel_id  SMALLINT  1 = 오프라인 매장, 2 = 온라인 (데이터로 추정한 해석)

주의
- 같은 날·같은 고객·같은 상품 행이 반복될 수 있습니다(약 18.7%).
  이는 수량으로 보며, 사용자가 요청하지 않으면 중복 제거하지 마십시오.
- 판매량 = 구매 기록 행 수(COUNT(*))입니다. 매출액으로 표현하지 마십시오.
- price는 비교용 지표입니다. 금액 단위를 붙이지 마십시오.
- 2020-03-20 ~ 2020-05-05에는 매장(채널 1) 구매가 거의 0건입니다(코로나19 휴업 시기).

----------------------------------------
2. hm.articles  (105,542행)
----------------------------------------
한 행: 상품 변형 1개 (색상 등으로 구분)

- article_id                    VARCHAR  PK
- product_code                  VARCHAR  상품 모델 코드 (여러 색상 변형을 묶음, 47,224개)
- prod_name                     VARCHAR  상품명 (영문)
- product_type_no / product_type_name       상품 유형 131종 (예: Sweater, Trousers, Dress)
- product_group_name            VARCHAR  상위 유형 19종 (예: Garment Upper body)
- graphical_appearance_no / _name           무늬·외관 30종 (예: Solid, Melange, Stripe)
- colour_group_code / colour_group_name     색 50종 (예: Black, Dark Blue, Light Beige)
- perceived_colour_value_id / _name         색의 밝기·톤 (예: Dark, Light, Dusty Light)
- perceived_colour_master_id / _name        대표 색 계열 20종 (예: Black, Beige, Brown)
- department_no / department_name           부서 250종
- index_code / index_name                   부문 10종
- index_group_no / index_group_name         최상위 부문 5종:
                                            Ladieswear, Divided, Menswear, Sport, Baby/Children
- section_no / section_name                 섹션 56종
- garment_group_no / garment_group_name     의류군 21종 (예: Knitwear, Jersey Basic, Trousers)
- detail_desc                   VARCHAR  영문 상품 설명 1~2문장 (결측 416건)

주의
- 코드(-1)나 'Unknown', 'undefined' 같은 대체값이 일부 있습니다.
- 상품 모델 단위 집계는 product_code, 색상별 집계는 article_id를 사용하십시오.

실제 값 목록 (WHERE 조건에는 아래 값을 정확히 그대로 쓰십시오. 없는 값을 만들면 0행이 됩니다)
- index_group_name (5): 'Ladieswear', 'Divided', 'Menswear', 'Sport', 'Baby/Children'
- index_name (10): 'Ladieswear', 'Ladies Accessories', 'Lingeries/Tights', 'Divided', 'Menswear',
                   'Sport', 'Children Sizes 92-140', 'Children Sizes 134-170', 'Baby Sizes 50-98',
                   'Children Accessories, Swimwear'
- product_group_name 주요값: 'Garment Upper body', 'Garment Lower body', 'Garment Full body',
                   'Accessories', 'Underwear', 'Shoes', 'Swimwear', 'Socks & Tights', 'Nightwear'
  (product_group_name에는 'Ladieswear' 같은 성별 값이 없습니다)
- garment_group_name 예: 'Knitwear', 'Jersey Basic', 'Jersey Fancy', 'Blouses', 'Trousers',
                   'Trousers Denim', 'Dresses Ladies', 'Outdoor', 'Shirts', 'Skirts', 'Shorts'
- department_name은 250종의 세부 부서명입니다(예: 'Knitwear', 'Blouse', 'Dress').
  'Ladies', 'Women' 같은 성별 값으로 필터하지 마십시오.

성별·대상 조건 매핑
- "여성", "여성복", "우먼"      → a.index_group_name = 'Ladieswear'
- "남성", "남성복"             → a.index_group_name = 'Menswear'
- "아동", "키즈", "베이비"      → a.index_group_name = 'Baby/Children'
- "스포츠"                     → a.index_group_name = 'Sport'
- 'Divided'는 10~20대 대상 별도 라인입니다. 사용자가 요청하지 않으면 여성에 포함하지 말고,
  scope_note에 "Divided 라인 제외"를 적으십시오.
- "의류"만 보고 싶다고 명시하면(액세서리·신발 제외) a.product_group_name LIKE 'Garment%' 를 추가하고
  scope_note에 적으십시오.
- 니트 = a.garment_group_name = 'Knitwear' (또는 product_type_name IN ('Sweater','Cardigan'))
- 값이 확실하지 않으면 정확 일치(=) 대신 lower(컬럼) LIKE '%키워드%' 를 쓰십시오.

----------------------------------------
3. hm.customers  (1,371,980행)
----------------------------------------
한 행: 고객 1명

- customer_id             VARCHAR  PK
- FN                      INTEGER  패션 뉴스(뉴스레터) 구독 여부. 1 또는 NULL (추정 해석)
- Active                  INTEGER  마케팅 연락 활성 여부. 1 또는 NULL (추정 해석)
- club_member_status      VARCHAR  ACTIVE / PRE-CREATE / LEFT CLUB / NULL
- fashion_news_frequency  VARCHAR  Regularly / Monthly / NONE / None / NULL
- age                     INTEGER  나이 (결측 약 1.2%)
- postal_code             VARCHAR  해시된 우편번호 (실제 지역을 알 수 없음)

주의
- FN, Active는 NULL을 0(해당 없음)으로 보십시오: COALESCE(FN, 0)
- postal_code로 실제 지역(서울·부산 등)을 추정하지 마십시오.

----------------------------------------
4. hm.article_images  (105,100행)
----------------------------------------
한 행: 상품 이미지 경로 1개 (상품당 최대 1개)
- article_id, image_location(파일 경로), storage_kind, source_archive

관계
- hm.transactions.customer_id → hm.customers.customer_id (N:1)
- hm.transactions.article_id  → hm.articles.article_id   (N:1)
- hm.article_images.article_id → hm.articles.article_id  (0..1:1)

H&M 대용량·JOIN 규칙
- hm.transactions(약 3,179만 행)의 원본 행을 그대로 반환하지 마십시오.
  조회 목적의 원본 행이 필요하면 LIMIT 100 이하로 제한하십시오.
- hm.transactions와 hm.articles의 JOIN은 가능하지만, 필요한 컬럼만 쓰고 GROUP BY로 바로 줄이십시오.
- 고객 속성(나이·회원 상태 등)이 필요하면 먼저 고객 단위로 집계한 뒤 hm.customers와 JOIN하십시오.
- 집계 alias 예: purchase_count, buyer_count, avg_price_index

H&M SQL 예시 (참고용, 그대로 재사용하지 말 것)

-- 상품 유형별 온라인 판매량 Top 10
SELECT a.product_type_name, COUNT(*) AS purchase_count
FROM hm.transactions t
JOIN hm.articles a ON t.article_id = a.article_id
WHERE t.sales_channel_id = 2
GROUP BY a.product_type_name
ORDER BY purchase_count DESC
LIMIT 10;

-- H&M 여성(Ladieswear) 상품 유형별 구매 건수 Top 10 + 전체 대비 구매 비중
SELECT a.product_type_name,
       COUNT(*) AS purchase_count,
       COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS purchase_share
FROM hm.transactions t
JOIN hm.articles a ON t.article_id = a.article_id
WHERE a.index_group_name = 'Ladieswear'
GROUP BY a.product_type_name
ORDER BY purchase_count DESC
LIMIT 10;

-- 월별 채널별 판매 추이
SELECT date_trunc('month', t_dat) AS month,
       CASE sales_channel_id WHEN 1 THEN '매장' WHEN 2 THEN '온라인' END AS channel,
       COUNT(*) AS purchase_count
FROM hm.transactions
GROUP BY 1, 2
ORDER BY 1, 2;

-- 고객별 구매 횟수 분포(히스토그램용, 고객 단위 표본)
SELECT *
FROM (
    SELECT customer_id, COUNT(*) AS purchase_count
    FROM hm.transactions
    GROUP BY customer_id
) AS customer_metrics
USING SAMPLE reservoir(100000 ROWS);

-- 연령대별 니트 구매 비중
WITH buyer AS (
    SELECT t.customer_id, COUNT(*) AS knit_count
    FROM hm.transactions t
    JOIN hm.articles a ON t.article_id = a.article_id
    WHERE a.garment_group_name = 'Knitwear'
    GROUP BY t.customer_id
)
SELECT CASE WHEN c.age < 25 THEN '~24' WHEN c.age < 35 THEN '25-34'
            WHEN c.age < 45 THEN '35-44' WHEN c.age < 55 THEN '45-54'
            WHEN c.age IS NOT NULL THEN '55+' ELSE 'unknown' END AS age_band,
       COUNT(*) AS buyer_count,
       SUM(b.knit_count) AS knit_purchase_count
FROM buyer b
JOIN hm.customers c ON b.customer_id = c.customer_id
GROUP BY 1
ORDER BY 1;
