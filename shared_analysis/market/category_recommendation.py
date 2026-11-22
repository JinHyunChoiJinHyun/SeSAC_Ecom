"""기존 비교표에 근거별 요약·조건부 후보 제안을 추가하는 순수 분석 모듈."""
from __future__ import annotations
import math
import pandas as pd


def summarize_naver(naver: pd.DataFrame, rows: list[dict], keyword_category: dict) -> dict:
    from shared_analysis.market import trend_analysis as ta
    if naver is None or naver.empty or 'keyword' not in naver:
        return {}
    keys = sorted({k for r in rows for k in r.get('naver_keywords', [])})
    if not keys:
        return {}
    summary = ta.analyze_sustainability(naver, keys)
    result = {}
    for entry in summary.get('rows', []):
        keyword = entry['keyword']
        if keyword not in keyword_category:
            continue
        growth = entry.get('recent_growth')
        result[keyword] = {
            'category': keyword_category[keyword],
            'recent_growth': growth,
            'growth_percent': round(100 * growth, 2) if growth is not None else None,
            'recent_mean': entry.get('recent_mean'),
            'prior_mean': entry.get('prior_mean'),
            'latest_period': entry.get('latest_period'),
            'recent_window': entry.get('recent_window'),
            'prior_window': entry.get('prior_window'),
            'judgement': entry.get('judgement'),
            'periods': entry.get('periods'),
            'unit': summary.get('period_unit'),
            'interpretation': '같은 네이버 요청 안의 상대 관심도 변화. 검색량/판매량 아님.',
        }
    return result


def musinsa_ranking_evidence(products: pd.DataFrame, aliases: dict) -> dict:
    """수집 범위/순위를 보여주되 순위 자체를 카테고리 간 인기 점수로 만들지 않는다."""
    if products is None or products.empty or 'category' not in products:
        return {}
    from shared_analysis.market.price_quality import analyze_metrics
    out = {}
    src = products.copy()
    src['mapped'] = src['category'].astype(str).map(lambda v: aliases.get(v.replace('_','/')))
    for category, part in src.groupby('mapped'):
        if category is None or pd.isna(category):
            continue
        values = pd.to_numeric(part['ranking'], errors='coerce') if 'ranking' in part else pd.Series(dtype='float64')
        out[category] = {
            'sample_products': int(part['item_id'].nunique()) if 'item_id' in part else len(part),
            'ranking_observed': int(values.notna().sum()),
            'market_metrics': analyze_metrics(part) if 'item_id' in part else None,
            'top_20_sample_products': int(values.le(20).sum()),
            'note': '순위는 카테고리 내 스냅샷 순위. 수집 규모·순위만으로 카테고리 간 매출/인기 우위 판단 불가',
        }
    return out


def recommend_with_evidence(rows: list[dict], naver_metrics: dict, ranking_evidence: dict, top_n: int = 3, *, period_plan=None) -> list[dict]:
    """후보를 설명 가능하게 선정. 관측된 최근 상승 신호를 우선 검토하되 비교 우위/지속 예측 금지."""
    top_n = max(1, int(top_n))
    candidates = []
    positive = set()
    for keyword, metric in naver_metrics.items():
        growth = metric.get('recent_growth')
        if isinstance(growth, (int, float)) and math.isfinite(growth) and growth >= 0.1:
            positive.add(metric['category'])
    for r in rows:
        c = r['category']
        purchases = int(r.get('hm_purchases') or 0)
        if period_plan is not None and purchases <= 0:
            # 구매 근거 없는 후보를 주력 상품 추천 순위에 올리지 않는다.
            continue
        models = int(r.get('hm_product_count') or 0)
        per_model = round(purchases / models, 2) if models else None
        trends = [dict(keyword=k, **v) for k, v in naver_metrics.items() if v.get('category') == c]
        observed = [t for t in trends if isinstance(t.get('recent_growth'), (int, float)) and math.isfinite(t['recent_growth'])]
        naver_status = ('상승 신호 확인' if c in positive else
                        '수집 키워드의 상승 신호 없음' if observed else
                        '수집/비교 가능한 키워드 없음')
        reasons = []
        if purchases:
            label = '과거 핵심 월' if period_plan else '과거 겨울철'
            reasons.append(f'H&M {label} 온라인 구매 기록 {purchases:,}건 (20~39세, 여성용 상품)')
        if naver_status == '수집/비교 가능한 키워드 없음':
            reasons.append('네이버 비교 가능한 키워드 미확보 (관심도가 낮다는 의미 아님)')
        if per_model is not None:
            reasons.append(f'모델당 구매 기록 약 {per_model:,.1f}건 (상품군 크기 차이 참고)')
        for k, v in naver_metrics.items():
            growth = v.get('recent_growth')
            if (v.get('category') == c and isinstance(growth, (int, float))
                    and math.isfinite(growth)):
                percent = v.get('growth_percent')
                if percent is None:
                    percent = growth * 100
                reasons.append(f'네이버 {k} 최근 상대 관심도 변화 {percent:+.2f}% (다른 키워드보다 높다는 의미 아님)')
        me = ranking_evidence.get(c, {})
        if me:
            reasons.append(f'무신사 해당 범주 수집 상품 {me.get("sample_products",0)}개 (판매량 근거 아님)')
        candidates.append({
            'category': c, 'hm_purchases': purchases, 'hm_product_count': models,
            'hm_purchases_per_model': per_model, 'naver': trends,
            'musinsa_ranking': me, 'reasons': reasons,
            'recent_positive_signal': c in positive,
            'naver_evidence_status': naver_status,
            'naver_observed_keyword_count': len(observed),
            'caveat': 'H&M 과거 실적과 최근 외부 관심은 서로 다른 시점/지표. 여성 고객 성별은 DB에서 확인 불가.',
        })
    # 추세가 있는 범주는 '우선 검토'로 올리되, 검증되지 않은 상대 인기 순위를 주장하지 않는다.
    candidates.sort(key=lambda r: (-int(r['recent_positive_signal']), -r['hm_purchases'], r['category']))
    return candidates[:min(top_n, len(candidates))]
