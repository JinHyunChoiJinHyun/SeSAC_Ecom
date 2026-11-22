"""무신사 카테고리 랭킹·상품정보·리뷰를 연결한 형태/관심사 근거.

상품명 규칙은 '디자인 표현' 분류에만 사용한다. 랭킹은 매출량/선호도 순위가 아니다.
"""
from __future__ import annotations

import re
from collections import Counter

import pandas as pd
from shared_analysis.market.price_quality import analyze_metrics, render_metrics

# 단어 포함 여부를 표시하는 중복 허용 특징이며, 소분류에 대한 확정 분류가 아니다.
SHAPES = {
    '후드형': r'후드|후디|hood|hooded',
    '푸퍼형': r'푸퍼|puffer',
    '봄버형': r'봄버|bomber|ma[- ]?1',
    '크롭형': r'크롭|cropped',
    '칼라형': r'칼라|카라|collar',
    '경량 표현': r'경량|라이트\s*(?:다운|패딩|재킷|자켓)|라이트다운|씬라이트|씬클라우드|lightweight|light down|ultralight',
    '패딩·다운 표현': r'패딩|다운|푸퍼|padded|padding|down|puffer|씬라이트|씬클라우드',
    '플리스 표현': r'플리스|fleece|후리스',
    '코트 표현': r'코트|coat',
}
REVIEW_TERMS = {
    '가벼움': r'가볍|가벼|무게|경량',
    '핏·사이즈': r'핏|사이즈|기장|길이|품이|여유|크기',
    '색감': r'색감|색상|컬러|색깔|색이|색은|색도',
    '보온성': r'따뜻|보온|추위|춥|온기',
    '두께감': r'두께|두껍|얇아|얇은|얇고|얇아서',
    '디자인': r'디자인|후드|카라|칼라|크롭|봄버|실루엣',
}
RELATED = {
    '경량패딩': r'경량|씬라이트|씬클라우드|라이트\s*(?:다운|패딩|재킷|자켓)|라이트다운|lightweight|light down|ultralight|푸퍼|패딩|다운|padded|puffer|down',
    '패딩': r'패딩|다운|푸퍼|padded|puffer|down|씬라이트',
    '코트': r'코트|coat',
    '플리스': r'플리스|후리스|fleece',
    '재킷': r'재킷|자켓|jacket|점퍼',
}


def _matching(series, pattern):
    return series.fillna('').astype(str).str.contains(pattern, regex=True, case=False, na=False)


def analyze_ranked(products: pd.DataFrame, info: pd.DataFrame,
                   reviews: pd.DataFrame, subtype: str = '경량패딩') -> dict:
    """단일 카테고리 랭킹의 여성·공용 우선 근거와 연결 리뷰를 계산한다."""
    if 'item_id' not in products or 'name' not in products or 'ranking' not in products:
        raise ValueError('무신사 랭킹에 item_id/name/ranking이 필요합니다.')
    p = products.copy()
    p['item_id'] = p['item_id'].astype(str)
    p['ranking'] = pd.to_numeric(p['ranking'], errors='coerce')
    p = p.dropna(subset=['ranking']).sort_values('ranking').drop_duplicates('item_id')
    if not info.empty and 'item_id' in info.columns:
        extras = info.copy()
        extras['item_id'] = extras['item_id'].astype(str)
        columns = [c for c in ('gender','rating','review_count') if c in extras]
        extras = extras[['item_id', *columns]].drop_duplicates('item_id')
        p = p.drop(columns=[c for c in columns if c in p]).merge(extras, on='item_id', how='left')
    if 'gender' not in p:
        p['gender'] = pd.NA
    # 성별 미확인 상품은 여성·공용으로 간주하지 않는다.
    gender_known = p['gender'].notna().any()
    eligible = p[p['gender'].isin(['여', '여성', '공용', '유니섹스', 'unisex'])].copy() if gender_known else p.copy()
    # '시어링'을 sheer(얇음)으로 오인하지 않고, 패딩/다운 표현을 경량 확정으로 보지 않는다.
    target_pattern = RELATED.get(str(subtype).strip())
    related = eligible[_matching(eligible['name'], target_pattern)].copy() if target_pattern else eligible.iloc[0:0].copy()
    # 경량 표현이 명시된 상품은 패딩·다운 등 넓은 연관 후보와 별도로 구분한다.
    lightweight = related[_matching(related['name'], SHAPES['경량 표현'])].copy()
    shapes = []
    for label, pattern in SHAPES.items():
        matched = eligible[_matching(eligible['name'], pattern)]
        examples = [{'rank': int(r['ranking']), 'name': str(r['name']), 'item_id': str(r['item_id'])}
                    for _, r in matched.head(3).iterrows()]
        shapes.append({'label': label, 'products': len(matched), 'examples': examples})
    related_shapes = []
    for label, pattern in SHAPES.items():
        matched = related[_matching(related['name'], pattern)]
        related_shapes.append({'label': label, 'products': len(matched)})
    r = reviews.copy()
    if 'item_id' not in r:
        r = pd.DataFrame(columns=['item_id', 'review'])
    r['item_id'] = r['item_id'].astype(str)
    r = r[r['item_id'].isin(related['item_id'])]
    if 'review_id' in r:
        r = r.drop_duplicates(['item_id', 'review_id'])
    elif 'review' in r:
        r = r.drop_duplicates(['item_id', 'review'])
    if 'review' not in r:
        r['review'] = ''
    aspects = [{'label': label, 'mentions': int(_matching(r['review'], pattern).sum()),
                'denominator': len(r)} for label, pattern in REVIEW_TERMS.items()]
    aspects.sort(key=lambda x: (-x['mentions'], x['label']))
    return {
        'subtype': subtype,
        'total_products': len(p), 'eligible_products': len(eligible),
        'gender_scope': '여성·공용' if gender_known else '성별 미확인(전체 참고)',
        'related_products': len(related), 'lightweight_named_products': len(lightweight),
        'related_reviews': len(r),
        'market_metrics': analyze_metrics(related, r),
        'ranked_examples': [{'rank': int(row['ranking']), 'name': str(row['name']), 'item_id': str(row['item_id'])}
                            for _, row in related.head(5).iterrows()],
        'shapes': shapes, 'related_shapes': related_shapes, 'aspects': aspects,
        'basis': '수집된 무신사 카테고리 랭킹 상위 표본과 상품명 규칙 매칭',
        'limitations': '매출·선호도 순위 아님. 상품명 매칭은 잠정 분류; 리뷰 언급은 긍정·부정을 구분하지 않음.',
    }


def load_ranked_evidence(category: str, subtype: str) -> dict:
    """현재 프로젝트의 카테고리별 CSV를 직접 읽는다(원본 수정 없음)."""
    from agent_system.common_tools.external_data import musinsa_sample
    from shared_analysis.market.category_comparison import normalize_category
    sets = [(key, value) for key, value in musinsa_sample.file_sets().items()
            if normalize_category(key) == category]
    if len(sets) != 1:
        raise ValueError('일치하는 무신사 카테고리 파일 세트가 정확히 1개여야 합니다.')
    files = sets[0][1]
    products = pd.read_csv(files['ranking'], dtype={'item_id': str})
    info = pd.read_csv(files['add_info'], dtype={'item_id': str}) if 'add_info' in files else pd.DataFrame()
    reviews = pd.read_csv(files['review'], dtype={'item_id': str, 'review_id': str}) if 'review' in files else pd.DataFrame()
    return analyze_ranked(products, info, reviews, subtype)


def render_ranked(evidence: dict) -> str:
    """마케터에게 표시할 데이터 범위·디자인 특성·관련 리뷰의 근거."""
    if not evidence:
        return '무신사 랭킹 상세 근거를 확인할 수 없습니다.'
    shapes = sorted((x for x in evidence['related_shapes'] if x['products']),
                    key=lambda x: -x['products'])[:5]
    scope = evidence['gender_scope']
    lines = [f"■ 무신사 상위 상품 디자인 분석 ({scope} 기준)",
             f"수집 랭킹 {evidence['total_products']}개 중 비교 대상 {evidence['eligible_products']}개, "
             f"'{evidence['subtype']}' 관련 상품명 규칙 매칭 {evidence['related_products']}개."]
    if evidence['ranked_examples']:
        lines.append('관련 상품 상위 순위 사례: ' + '; '.join(
            f"{x['rank']}위 {x['name']}" for x in evidence['ranked_examples'][:3]))
    if evidence['subtype'] == '경량패딩':
        lines.append(f"이 중 경량을 명시한 상품명 {evidence['lightweight_named_products']}개. "
                     '나머지 패딩·다운 관련 상품까지 경량패딩으로 확정하지 않습니다.')
    if shapes:
        lines.append('관련 상품에서 나타난 디자인 표현(중복 집계): ' + ', '.join(
            f"{x['label']} {x['products']}개" for x in shapes))
    lines.append(f"관련 상품에 연결된 수집 리뷰 {evidence['related_reviews']}건 기준 관심 표현:")
    relevant = [x for x in evidence['aspects'] if x['mentions'] > 0]
    lines.append(', '.join(f"{x['label']} {x['mentions']}건 ({x['mentions']/x['denominator']:.1%})" for x in relevant if x['denominator']) if relevant else '확인 가능한 리뷰 표현 없음')
    if evidence.get('market_metrics'):
        lines.extend(['', '■ 관련 상품 가격·할인·평점·리뷰 지표', *render_metrics(evidence['market_metrics'])])
    lines.append('※ 랭킹은 매출 순위가 아니며 상품명 분류는 잠정적입니다. 리뷰 언급은 선호·긍정 평가가 아닙니다.')
    return '\n'.join(lines)
