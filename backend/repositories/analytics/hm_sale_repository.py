"""
backend/repositories/analytics/hm_sales_repository.py
[Duck] daily_purchases 집계 — 조회만 한다. 순위·일평균·판매 0 채우기는 shared_analysis/product/product_analysis.py

hm.daily_purchases 전제 (database/analytics/schema/02_hm_processed.sql)
  CREATE OR REPLACE TABLE hm.daily_purchases AS
  SELECT t_dat, customer_id, article_id, sales_channel_id,
         COUNT(*)   AS qty,     -- 반복 행 수 (보고서의 추정 해석)
         AVG(price) AS price    -- qty * price = 원본 거래의 SUM(price)
  FROM hm.transactions
  GROUP BY t_dat, customer_id, article_id, sales_channel_id
  ORDER BY t_dat, article_id;
  price를 AVG가 아닌 MIN·첫 행 값으로 바꾸면 price_index_sum이 원본과 어긋난다
"""

from datetime import date
from typing import Any
from sql_utils import (
    build_in_clause,
    fetch_dicts
)

DAILY_PURCHASES = "hm.daily_purchases"

class HMSaleRepository:
    def __init__(self, connection):
        self.connection = connection

    def _build_conditions(
            self,
            article_ids: list[str],
            start: date,
            end: date,
            channel_ids: list[int] | None
    ) -> tuple[str, list[Any]]:
        """상품별 기간 및 판매처 필터 조건 생성"""
        if not article_ids:
            raise ValueError("article_ids가 비어 있습니다")
        clause, params = build_in_clause("article_id", article_ids) # SQL Injection 방지를 위해 column명 하드코딩
        conditions = [clause, "t_dat BETWEEN ? AND ?"] # t_dat는 daily_purchases의 컬럼 가정
        params += [start, end]
        if channel_ids:
            ch_clause, ch_params = build_in_clause("sales_channel_id", channel_ids)
            conditions.append(ch_clause)
            params += ch_params
        return " AND ".join(conditions), params

    def totals(
            self,
            article_ids: list[str],
            start: date,
            end: date,
            channel_ids: list[int] | None
    ) -> list[dict[str,Any]]:
        """기간 내 판매가 있는 상품만 반환 (판매가 없는 경우 0으로 채움)"""
        where, params = self._build_conditions(article_ids, start, end, channel_ids)
        return fetch_dicts(
            f"""
            SELECT
                article_id,
                CAST(SUM(qty) AS BIGINT) AS qty,
                COUNT(DISTINCT customer_id) AS buyers,
                SUM(qty * price) AS price_index_sum,
                MIN(t_dat) AS first_sale_date
            FROM {DAILY_PURCHASES}
            WHERE {where}
            GROUP BY article_id
            """,
            params
        )

    def weekly(
            self,
            article_ids: list[str],
            start: date,
            end: date,
            channel_ids: list[int] | None
    ) -> list[dict[str, Any]]:
        """주 단위 내 판매가 있는 상품만 반환"""
        where, params = self._build_conditions(article_ids, start, end, channel_ids)
        return fetch_dicts(
            f"""
            SELECT
                article_id,
                GREATEST(CAST(date_trunc('week', t_dat) AS DATE), CAST(? AS DATE)) AS week_start,
                CAST(SUM(qty) AS BIGINT) AS qty,
                COUNT(DISTINCT customer_id) AS buyers
            FROM {DAILY_PURCHASES}
            WHERE {where}
            GROUP BY article_id, week_start,
            ORDER BY article_id, week_start
            """,
            [start] + params
        )