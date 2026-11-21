"""H&M 5개 범주와 20~39세 온라인 구매 내역의 과거 실적 집계 (읽기 전용)."""
from __future__ import annotations
from datetime import date
import pandas as pd
from shared_analysis.product.category_mapping import CATEGORIES, categorize


def winter_dates(years=(2018, 2019, 2020)) -> list[tuple[str, str]]:
    """H&M 보유 범위 안에서 겨울 12~2월을 조회할 수 있도록 3개월 묶음 반환."""
    return [(f'{year}-12-01', f'{year + 1}-02-28') for year in years]


def category_sales(age_min: int = 20, age_max: int = 39, channel: str = 'online',
                   winter_only: bool = True) -> dict:
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
    con = get_analysis_connection()
    try:
        # 고객의 구매 당시 나이·성별은 없음. 저장된 age만 사용합니다.
        q = ("SELECT t.article_id, COUNT(*) AS purchases "
             "FROM hm.transactions t JOIN hm.customers c ON c.customer_id = t.customer_id "
             "WHERE t.sales_channel_id IN (" + ','.join('?' for _ in hm_products.CHANNELS[channel]) + ") "
             "AND c.age BETWEEN ? AND ? AND t.t_dat <= ?")
        parameters = [*hm_products.CHANNELS[channel], age_min, age_max, hm_products.data_as_of().isoformat()]
        if winter_only:
            q += ' AND EXTRACT(MONTH FROM t.t_dat) IN (12, 1, 2)'
        # 반환 전 필터링하므로 다른 상품 구매 실적은 집계에서 제외됩니다.
        q += ' GROUP BY t.article_id'
        transactions = con.execute(q, parameters).df()
    finally:
        con.close()
    transactions['article_id'] = transactions['article_id'].astype(str)
    joined = chosen.merge(transactions, on='article_id', how='left')
    joined['purchases'] = joined['purchases'].fillna(0).astype('int64')
    grouped = joined.groupby('musinsa_category', as_index=True).agg(
        article_count=('article_id', 'nunique'), product_count=('product_code', 'nunique'),
        purchases=('purchases', 'sum'))
    grouped = grouped.reindex(CATEGORIES, fill_value=0)
    rows = [{'category': name, **{key: int(value) for key, value in grouped.loc[name].items()}}
            for name in CATEGORIES]
    return {'rows': rows, 'channel': channel, 'age_min': age_min, 'age_max': age_max,
            'winter_only': winter_only, 'data_as_of': str(hm_products.data_as_of()),
            'note': ('20~39세 고객이 온라인에서 구매한 여성용 상품의 과거 구매 건수. '
                     '성별·구매 당시 나이는 확인 불가. 겨울=12·1·2월. 구매건수는 거래 레코드 수이며 수량과 완전히 같다고 보장하지 않음.')}
