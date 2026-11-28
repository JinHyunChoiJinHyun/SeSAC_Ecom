"""무신사 표본의 가격·할인·평점·리뷰 지표 (판매량 추정 금지)."""
from __future__ import annotations
import pandas as pd


def _numbers(frame, column, lower=0, upper=None, *, positive=False, integer=False):
    if column not in frame:
        return pd.Series(dtype=float)
    values = pd.to_numeric(frame[column], errors='coerce').replace([float('inf'), -float('inf')], float('nan')).dropna()
    values = values[values.gt(lower) if positive else values.ge(lower)]
    if upper is not None:
        values = values[values.le(upper)]
    if integer:
        values = values[values.mod(1).eq(0)]
    return values


def _summary(values, total):
    n = len(values)
    return {'valid': n, 'missing_or_invalid': total-n,
            'median': round(float(values.median()), 2) if n else None,
            'mean': round(float(values.mean()), 2) if n else None,
            'q1': round(float(values.quantile(.25)), 2) if n else None,
            'q3': round(float(values.quantile(.75)), 2) if n else None}


def analyze_metrics(products, reviews=None):
    """같은 카테고리 내 상품 ID 중복 제거, 각 지표의 유효 분모 명시."""
    p = products.copy()
    if 'item_id' not in p:
        raise ValueError('무신사 상품 ID 열 누락')
    p = p[p['item_id'].notna()].copy()
    p['item_id'] = p['item_id'].astype(str)
    p = p.drop_duplicates('item_id')
    n = len(p)
    metrics = {}
    for col in ('price', 'best_price', 'original_price'):
        metrics[col] = _summary(_numbers(p,col,positive=True), n)
    metrics['discount_rate'] = _summary(_numbers(p,'discount_rate',upper=100),n)
    metrics['rating'] = _summary(_numbers(p,'rating',upper=5,positive=True),n)
    counts = _numbers(p,'review_count',integer=True)
    metrics['review_count'] = _summary(counts,n)
    metrics['review_count']['sum'] = int(counts.sum()) if len(counts) else None
    # 표시 가격 기준 할인율과 원본 할인율의 일치 여부만 진단한다. 쿠폰가와 혼용하지 않는다.
    rate = _numbers(p,'discount_rate',upper=100)
    price = _numbers(p,'price',positive=True)
    original = _numbers(p,'original_price',positive=True)
    aligned = pd.concat([rate.rename('rate'), price.rename('price'), original.rename('original')],axis=1).dropna()
    aligned = aligned[aligned.price.le(aligned.original)]
    mismatch = (aligned.rate - (1-aligned.price/aligned.original)*100).abs().gt(1)
    r = reviews.copy() if reviews is not None else None
    linked = None
    reviewed_items = None
    if r is not None and 'item_id' in r:
        r = r[r['item_id'].notna()].copy()
        r['item_id'] = r['item_id'].astype(str)
        r = r[r['item_id'].isin(p['item_id'])]
        if 'review_id' in r:
            known = r[r['review_id'].notna()].drop_duplicates(['item_id','review_id'])
            unknown = r[r['review_id'].isna()]
            if 'review' in unknown:
                unknown = unknown.drop_duplicates(['item_id','review'])
            r = pd.concat([known,unknown])
        elif 'review' in r:
            r = r.drop_duplicates(['item_id','review'])
        linked, reviewed_items = len(r), int(r.item_id.nunique())
    return {'sample_products':n,'metrics':metrics,'discount_check':{'comparable':len(aligned),'difference_over_1pp':int(mismatch.sum())},
            'linked_reviews':linked,'items_with_linked_reviews':reviewed_items,
            'note':'수집 표본의 기술통계. 상품별 표시 리뷰 수와 수집 리뷰는 다르며 판매량·현재 가격·시장 점유율이 아님.'}


def _metric_number(value, unit):
    """통화·건수는 정수이면 소수점을 생략하되, 분포 분위수는 버리지 않는다."""
    if unit in ('원', '건') and float(value).is_integer():
        return f'{int(value):,}'
    return f'{value:,.2f}'


def render_metrics(evidence):
    if not evidence:
        return []
    total = evidence['sample_products']
    labels = {'price':'표시 가격','best_price':'최저 혜택가','original_price':'정가','discount_rate':'표시 할인율','rating':'상품 평점(5점 기준)','review_count':'상품별 표시 리뷰 수'}
    units = {'price':'원','best_price':'원','original_price':'원','discount_rate':'%','rating':'점','review_count':'건'}
    lines = []
    for key,label in labels.items():
        m=evidence['metrics'][key]
        if m['median'] is None:
            lines.append(f'{label}: 유효 자료 없음 (0/{total}개)')
        else:
            unit=units[key]
            lines.append(f"{label} 중앙값 {_metric_number(m['median'], unit)}{unit} (25~75% 구간 {_metric_number(m['q1'], unit)}~{_metric_number(m['q3'], unit)}{unit}, 유효 {m['valid']}/{total}개)")
    n=evidence.get('linked_reviews')
    if n is not None:
        lines.append(f"실제 수집·연결 리뷰 {n:,}건 / 리뷰 연결 상품 {evidence['items_with_linked_reviews']}/{total}개")
    check=evidence['discount_check']
    lines.append(f"표시 할인율 검산: 비교 가능 {check['comparable']}개 중 1%p 초과 차이 {check['difference_over_1pp']}개 (정가·표시 가격 기준)")
    lines.append(evidence['note'])
    return lines
