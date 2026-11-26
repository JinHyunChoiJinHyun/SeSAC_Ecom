"""기획전 일정과 동일한 날짜 구간의 관측 구매량을 검증해 상품군별로 요약한다."""
from __future__ import annotations
import pandas as pd


def summarize_campaign_window(weekly: pd.DataFrame, rows: list[dict]) -> dict:
    """관측 주차 중복·누락 및 부분 주차를 점검하고 상품군 합계를 반환한다."""
    required = {'category', 'season', 'week_index', 'observed_days', 'purchases'}
    if weekly is None or weekly.empty or not required.issubset(weekly.columns):
        raise ValueError('기획전 대응 기간 구매 근거 칼럼 또는 관측값이 없습니다.')
    result = {}
    expected_categories = [row['category'] for row in rows]
    for category in expected_categories:
        subset = weekly.loc[weekly['category'].eq(category)]
        if subset.empty or subset.duplicated(['season', 'week_index']).any():
            raise ValueError(f'{category} 시즌·주차 기록 누락 또는 중복')
        seasons = sorted(subset['season'].unique().tolist())
        if len(seasons) != 2:
            raise ValueError(f'{category} 과거 대응 기간 두 개가 모두 필요합니다.')
        totals = []
        for season in seasons:
            s = subset.loc[subset['season'].eq(season)].sort_values('week_index')
            indices = [int(i) for i in s['week_index']]
            if indices != list(range(1, len(indices) + 1)):
                raise ValueError(f'{category} {season}시즌 주차 누락')
            days = pd.to_numeric(s['observed_days'], errors='coerce')
            purchases = pd.to_numeric(s['purchases'], errors='coerce')
            if days.isna().any() or purchases.isna().any() or (days <= 0).any() or (days > 7).any() or (purchases < 0).any():
                raise ValueError(f'{category} 기간 또는 구매 기록 오류')
            if ((days != 7) & (s['week_index'] != s['week_index'].max())).any():
                raise ValueError(f'{category} 중간 주차에 부분 관측 구간 존재')
            totals.append((int(days.sum()), int(purchases.sum())))
        if len({t[0] for t in totals}) != 1:
            raise ValueError(f'{category} 비교하는 두 시즌의 관측 일수가 다릅니다.')
        count = sum(t[1] for t in totals)
        total_days = sum(t[0] for t in totals)
        result[category] = {'purchases': count, 'observed_days': total_days,
                            'days_per_season': totals[0][0], 'purchases_per_day': count/total_days}
    return result
