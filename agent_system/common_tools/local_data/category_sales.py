"""H&M 5개 범주와 20~39세 온라인 구매 내역의 과거 실적 집계 (읽기 전용)."""
from __future__ import annotations
from datetime import date
import pandas as pd
from shared_analysis.product.category_mapping import CATEGORIES, categorize


def winter_dates(years=(2018, 2019, 2020)) -> list[tuple[str, str]]:
    """H&M 보유 범위 안에서 겨울 12~2월을 조회할 수 있도록 3개월 묶음 반환."""
    return [(f'{year}-12-01', f'{year + 1}-02-28') for year in years]


def category_sales(age_min: int = 20, age_max: int = 39, channel: str = 'online',
                   winter_only: bool = True, *, period_plan: dict | None = None, scope: str = "core") -> dict:
    """실측 과거 구매 집계. 고객 성별은 원본 DB에 없으므로 필터하지 않습니다.

    가격과 미래 판매량 예측은 수행하지 않습니다. H&M data_as_of를 넘는 날짜는 조회하지 않습니다.
    """
    from agent_system.common_tools.local_data import hm_products
    from common.db_client import get_analysis_connection
    if not (0 <= age_min <= age_max <= 120):
        raise ValueError('연령 조건이 올바르지 않습니다')
    if channel not in hm_products.CHANNELS:
        raise ValueError('알 수 없는 구매 채널입니다')
    products = categorize(hm_products.articles())
    chosen = products.loc[products['musinsa_category'].notna(), ['article_id', 'musinsa_category', 'product_code']]
    from agent_system.common_tools.local_data.campaign_period import purchase_date_filter
    cutoff = hm_products.data_as_of()
    date_sql, date_params = purchase_date_filter(cutoff, winter_only=winter_only, period_plan=period_plan, scope=scope)
    con = get_analysis_connection()
    try:
        # 고객의 구매 당시 나이·성별은 없음. 저장된 age만 사용합니다.
        q = ("SELECT t.article_id, COUNT(*) AS purchases "
             "FROM hm.transactions t JOIN hm.customers c ON c.customer_id = t.customer_id "
             "WHERE t.sales_channel_id IN (" + ','.join('?' for _ in hm_products.CHANNELS[channel]) + ") "
             "AND c.age BETWEEN ? AND ?" + date_sql)
        parameters = [*hm_products.CHANNELS[channel], age_min, age_max, *date_params]
        # 반환 전 필터링하므로 다른 상품 구매 실적은 집계에서 제외됩니다.
        q += ' GROUP BY t.article_id'
        transactions = con.execute(q, parameters).df()
    finally:
        con.close()
    transactions['article_id'] = transactions['article_id'].astype(str)
    if period_plan is not None:
        chosen = chosen.copy()
        chosen['article_id'] = chosen['article_id'].astype(str).str.zfill(10)
        transactions['article_id'] = transactions['article_id'].str.zfill(10)
    joined = chosen.merge(transactions, on='article_id', how='left')
    joined['purchases'] = joined['purchases'].fillna(0).astype('int64')
    if period_plan is not None:
        # 핵심/사전 월에 실제 구매 근거가 있는 모델 수만 집계한다.
        joined = joined.loc[joined['purchases'] > 0]
    grouped = joined.groupby('musinsa_category', as_index=True).agg(
        article_count=('article_id', 'nunique'), product_count=('product_code', 'nunique'),
        purchases=('purchases', 'sum'))
    grouped = grouped.reindex(CATEGORIES, fill_value=0)
    rows = [{'category': name, **{key: int(value) for key, value in grouped.loc[name].items()}}
            for name in CATEGORIES]
    return {'rows': rows, 'channel': channel, 'age_min': age_min, 'age_max': age_max,
            'winter_only': winter_only if period_plan is None else False, 'data_as_of': str(cutoff),
            **({'period_plan': period_plan, 'period_scope': scope} if period_plan is not None else {}),
            'note': ('20~39세 고객이 온라인에서 구매한 여성용 상품의 과거 구매 건수. '
                     '성별·구매 당시 나이는 확인 불가. ' +
                     (f'과거 월 단위 분석 기간({scope}). ' if period_plan is not None else '겨울=12·1·2월. ' if winter_only else '전체 관측 기간. ') +
                     '구매건수는 거래 레코드 수이며 수량과 완전히 같다고 보장하지 않음.')}
