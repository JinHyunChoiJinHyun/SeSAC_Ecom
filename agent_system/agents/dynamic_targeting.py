"""사용자 요청 우선의 타깃 분석. 모델의 예측 순위와 카테고리 구매 이력 순위를 구분한다."""
from __future__ import annotations

import re
from pathlib import Path

MODEL_DATE = '2020-09-23'
# 별칭은 DB의 garment_group_name과 동일한지 매 실행 시 확인한다.
CATEGORY_ALIASES = {'아우터': 'Outdoor', 'outerwear': 'Outdoor', 'outer': 'Outdoor'}
# H&M에서 정확한 품목·색상으로 확인 가능한 표현만 정규화한다. 포괄적인 '니트'는 자동 치환하지 않는다.
EXACT_SYNONYMS = {'스웨터': 'Sweater', '블랙': 'Black', '그리니시 카키': 'Greenish Khaki'}


class TargetSelectionError(ValueError):
    """요청을 안전하게 매핑하지 못했거나 데이터 근거가 없는 경우."""


def _values(campaign):
    values = (campaign or {}).get('values') or {}
    return values if isinstance(values, dict) else {}


def explicit_combo_in_question(question: str, combos) -> dict | None:
    """지원 조합과 정확하게 일치하는 품목·색상이 질문에 둘 다 있을 때만 선택."""
    text = str(question or '').casefold()
    for korean, english in EXACT_SYNONYMS.items():
        text = text.replace(korean, ' ' + english.casefold() + ' ')
    found = []
    for row in combos.itertuples(index=False):
        pt, colour = str(row.product_type), str(row.colour)
        # 영문 토큰 경계를 검사해 일부 단어 오탐을 피한다.
        pat_pt = r'(?<![\w])' + re.escape(pt.casefold()) + r'(?![\w])'
        pat_colour = r'(?<![\w])' + re.escape(colour.casefold()) + r'(?![\w])'
        if re.search(pat_pt, text) and re.search(pat_colour, text):
            found.append((pt, colour))
    found = list(dict.fromkeys(found))
    if len(found) > 1:
        raise TargetSelectionError('MULTIPLE_EXPLICIT_COMBOS')
    return dict(zip(('product_type', 'colour'), found[0])) if found else None


def request_category(question: str, campaign: dict) -> str | None:
    """현재 질문의 카테고리를 우선하고 없으면 확정 캠페인을 읽는다."""
    text = str(question or '').casefold()
    for key, group in CATEGORY_ALIASES.items():
        # 한국어 조사(아우터를/아우터로)를 허용한다. 영문은 단어 경계로 검사한다.
        if (key == '아우터' and key in text) or (key != '아우터' and re.search(r'(?<![\w])' + re.escape(key) + r'(?![\w])', text)):
            return group
    values = _values(campaign)
    # 카테고리 조회를 명시한 경우에만 캠페인 카테고리를 사용한다.
    if not re.search(r'타깃|타겟|고객|구매|선정|추천|순위|target', text):
        return None
    raw = str(values.get('category') or values.get('target_category') or '').strip()
    return CATEGORY_ALIASES.get(raw.casefold(), raw or None)


def resolve_user_selection(question: str, campaign: dict, supported) -> dict:
    """명시적 ID→명시적 품목×색상→현재 질문 카테고리→캠페인 순으로 선택."""
    from agent_system.agents.model_targeting_agent import ID_RE
    ids = list(dict.fromkeys(ID_RE.findall(question or '')))
    if len(ids) > 1:
        raise TargetSelectionError('MULTIPLE_ARTICLE_IDS')
    if ids:
        return {'mode': 'article', 'article_id': ids[0]}
    selected = explicit_combo_in_question(question, supported)
    if selected:
        return {'mode': 'combo', **selected}
    # 사용자가 품목만 명시했는데 색상이 없다면 다른 캠페인 품목으로 대체하지 않는다.
    # 예: 'Sweater 고객 추천'은 Sweater의 색상을 확인해야 한다.
    text = str(question or '').casefold()
    for korean, english in EXACT_SYNONYMS.items():
        text = text.replace(korean, ' ' + english.casefold() + ' ')
    mentioned_types = {str(x).casefold() for x in supported['product_type']
                       if re.search(r'(?<![\w])' + re.escape(str(x).casefold()) + r'(?![\w])', text)}
    if mentioned_types:
        raise TargetSelectionError('EXPLICIT_PRODUCT_TYPE_REQUIRES_COLOUR')
    # 명시된 카테고리 요청은 캠페인에 저장된 다른 품목을 덮어쓴다.
    text = str(question or '').casefold()
    if any((k == '아우터' and k in text) or (k != '아우터' and re.search(r'(?<![\w])' + re.escape(k) + r'(?![\w])', text)) for k in CATEGORY_ALIASES):
        return {'mode': 'category_history', 'group': request_category(question, {})}
    v = _values(campaign)
    pt, colour = v.get('target_product_type'), v.get('target_colour')
    if isinstance(pt, str) and pt.strip() and isinstance(colour, str) and colour.strip():
        pair = (pt.strip(), colour.strip())
        accepted = set(zip(supported['product_type'], supported['colour']))
        if pair not in accepted:
            raise TargetSelectionError('CAMPAIGN_COMBO_UNSUPPORTED')
        return {'mode': 'combo', 'product_type': pair[0], 'colour': pair[1]}
    group = request_category(question, campaign)
    if group:
        return {'mode': 'category_history', 'group': group}
    raise TargetSelectionError('TARGET_SELECTION_REQUIRED')


def summarize_category_history(frame, as_of: str = MODEL_DATE) -> dict:
    """선정된 계열의 구매 고객 *전체*를 구매횟수·최근성으로 배타적으로 집계한다.

    기간은 기준일 직전 90일이며, 다른 품목의 구매 기록은 포함하지 않는다.
    이 결과는 마케팅 수신 동의나 구매 예측 확률을 뜻하지 않는다.
    """
    import pandas as pd
    if frame.empty:
        raise TargetSelectionError('CATEGORY_SEGMENT_EMPTY')
    purchases = pd.to_numeric(frame['category_purchases'], errors='raise')
    if purchases.isna().any() or (purchases < 1).any():
        raise TargetSelectionError('CATEGORY_SEGMENT_PURCHASE_INVALID')
    dates = pd.to_datetime(frame['last_category_purchase'], errors='raise')
    recency = (pd.Timestamp(as_of) - dates).dt.days
    if dates.isna().any() or not recency.between(1, 90).all():
        raise TargetSelectionError('CATEGORY_SEGMENT_DATE_INVALID')
    masks = (
        ('최근·반복 구매', (recency <= 30) & (purchases >= 2), '최근 30일 안에 해당 계열 2건 이상 구매'),
        ('최근·1회 구매', (recency <= 30) & (purchases == 1), '최근 30일 안에 해당 계열 1건 구매'),
        ('이전·반복 구매', (recency > 30) & (purchases >= 2), '31~90일 전에 마지막 구매, 90일 동안 2건 이상'),
        ('이전·1회 구매', (recency > 30) & (purchases == 1), '31~90일 전에 마지막 구매, 90일 동안 1건'),
    )
    total = len(frame)
    rows = [{'name': name, 'count': int(mask.sum()),
             'share_percent': round(100 * int(mask.sum()) / total, 2), 'basis': basis}
            for name, mask, basis in masks]
    if sum(row['count'] for row in rows) != total:
        raise TargetSelectionError('CATEGORY_SEGMENT_TOTAL_MISMATCH')
    return {'kind': 'category_purchase_recency', 'total': total, 'segments': rows,
            'average_category_purchases': round(float(purchases.mean()), 2),
            'basis': '기준일 직전 90일 해당 카테고리 구매횟수와 마지막 구매일(전 고객 집계)',
            'limits': '아우터 계열 구매 이력 기반 구간이며 구매 예측 확률·실제 발송 가능 인원을 뜻하지 않음'}


def category_history(data, group: str, as_of: str = MODEL_DATE, limit: int = 20):
    """카테고리 구매 이력 기반 순위를 계산한다. 구매 예측 모델의 점수가 아니다."""
    if as_of != MODEL_DATE or type(limit) is not int or not 1 <= limit <= 100:
        raise TargetSelectionError('HISTORY_REQUEST_INVALID')
    # 실제 DB에 카테고리 컬럼·값이 존재하는지 확인 후 실행한다.
    cols = {r[1] for r in data.con.execute("PRAGMA table_info('hm.articles')").fetchall()}
    if 'garment_group_name' not in cols:
        raise TargetSelectionError('GARMENT_GROUP_COLUMN_MISSING')
    available = {r[0] for r in data.con.execute(
        'SELECT DISTINCT garment_group_name FROM hm.articles WHERE garment_group_name IS NOT NULL'
    ).fetchall()}
    if group not in available:
        raise TargetSelectionError('CATEGORY_NOT_FOUND_IN_HM')
    # 모델 입력과 동일한 과거 90일 창만 사용한다. 거래 당일 자료는 포함되지 않는다.
    query = '''
      SELECT t.customer_id,
             COUNT(*)::BIGINT AS category_purchases,
             COUNT(DISTINCT t.t_dat::DATE)::BIGINT AS category_purchase_days,
             MAX(t.t_dat::DATE) AS last_category_purchase
      FROM hm.transactions AS t
      JOIN hm.articles AS a ON t.article_id = a.article_id
      WHERE a.garment_group_name = ?
        AND t.t_dat::DATE >= (?::DATE - INTERVAL 90 DAY)
        AND t.t_dat::DATE < ?::DATE
      GROUP BY t.customer_id
      ORDER BY category_purchases DESC, last_category_purchase DESC, t.customer_id ASC
    '''
    df = data.con.execute(query, [group, as_of, as_of]).df()
    if df.empty:
        raise TargetSelectionError('CATEGORY_HISTORY_EMPTY')
    segment_summary = summarize_category_history(df, as_of)
    # score = 순위 백분위 표시용. 모델 확률이 아니라는 점을 결과에도 명시한다.
    count = len(df)
    top = df.head(limit).copy()
    top['rank'] = list(range(1, len(top) + 1))
    top.attrs['segment_summary'] = segment_summary
    top['score'] = [float(1 - (i / count)) for i in range(len(top))]
    return top, count


def to_category_result(group: str, top, population: int) -> dict:
    rows = [
        {'customer_id': str(r.customer_id), 'score': float(r.score),
         'rank': int(r.rank), 'category_purchases': int(r.category_purchases),
         'category_purchase_days': int(r.category_purchase_days)}
        for r in top.itertuples(index=False)
    ]
    return {
        'final_answer': (
            f'[과거 구매 이력 기반 후보] H&M {group} 계열 / 기준일 {MODEL_DATE} 이전 90일 / '
            f'해당 계열 구매 고객 {population:,}명 / 상위 {len(rows)}명 미리보기. '
            '구매 횟수→최신 구매일→고객 ID 순이며, score는 순위 표시값일 뿐 '
            '구매 예측 모델 점수나 구매 확률이 아닙니다. '
            '2026~2027년 기획전 실제 발송 후보로 확정되지 않았습니다.'
        ),
        'target_result': {
            'purpose': 'rank_only', 'rows': rows, 'category_group': group,
            'score_as_of': MODEL_DATE, 'total_scored_customers': population,
            'selection_basis': 'hm_category_purchase_history_90d',
            'score_interpretation': 'historical_purchase_rank_percentile_not_model',
            'segment_summary': top.attrs.get('segment_summary'),
            'send_eligible_verified': False,
        },
        'dataframe': top,
        'data_sources': ['hm'],
        'data_purpose': '사용자 요청 카테고리의 과거 90일 구매 고객 탐색',
        'data_scope_note': '모델 예측 점수와 구별되는 규칙 기반 순위. 수신 동의·발송 상한·연령 조건 미검증.',
    }


def calculate_requested_target(question: str, campaign: dict, repo_root: Path) -> dict:
    from modeling.purchase_model import connect_hm, score_article, score_combo, select_model, supported_combos
    from agent_system.agents.model_targeting_agent import to_candidate_result
    model = select_model(MODEL_DATE)
    selection = resolve_user_selection(question, campaign, supported_combos(model))
    with connect_hm() as data:
        if selection['mode'] == 'article':
            result = score_article(data, selection['article_id'], MODEL_DATE)
        elif selection['mode'] == 'combo':
            result = score_combo(data, selection['product_type'], selection['colour'], MODEL_DATE)
        else:
            top, population = category_history(data, selection['group'])
            return to_category_result(selection['group'], top, population)
    return to_candidate_result(result)
