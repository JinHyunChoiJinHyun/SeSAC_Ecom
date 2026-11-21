"""13~16단계: 검증된 소구점에 근거한 간결한 승인 설명과 대표 문구 3안.

외부 LLM 없이 재현 가능한 문구를 생성한다. 미확인 가격·성능·할인을 추정하지 않는다.
"""
from __future__ import annotations

import json
import re


def _selected(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or '')
    except (TypeError, ValueError) as exc:
        raise ValueError('13단계 승인된 소구점 구성이 없습니다.') from exc
    if not isinstance(result, dict):
        raise ValueError('13단계 소구점 구성 형식이 올바르지 않습니다.')
    return result


def _colors(campaign):
    raw = (campaign.get('values') or {}).get('colors') or ''
    if isinstance(raw, list):
        colors = raw
    else:
        colors = [x.strip() for x in re.split(r'[,，]', str(raw)) if x.strip()]
    return [str(x).strip() for x in colors if str(x).strip()]


def selection_brief(summary, selected, campaign, market=None):
    """승인 버튼 앞에서 선택 소구점의 적용 방향을 자연어로 설명한다."""
    selected = _selected(selected)
    points = selected.get('points') or []
    known = {c['label'] for c in summary.get('candidates', [])}
    if not 2 <= len(points) <= 3 or not set(points).issubset(known):
        raise ValueError('12단계 추천에 없는 소구점입니다.')
    values = campaign.get('values') or {}
    subtype = values.get('subtype') or values.get('category') or '선정 상품'
    colors = _colors(campaign)
    color_text = '·'.join(colors) if colors else '확인된 상품 색상'
    market = market or {}
    aspects = sorted((r for r in (market.get('saved_rows') or [])
                      if r.get('source') == 'musinsa' and r.get('aspect')
                      and isinstance(r.get('mention_reviews'), (float, int))),
                     key=lambda r: -r['mention_reviews'])[:4]
    sample = market.get('sample_reviews')
    if aspects and sample:
        interest = ', '.join(f"{r['aspect']} {int(r['mention_reviews']):,}건" for r in aspects)
        market_text = f"무신사 {market.get('category') or '경쟁 카테고리'} 전체 리뷰 {int(sample):,}건에서 {interest} 등이 언급됐습니다. "
    else:
        market_text = '경쟁 리뷰에서는 상품의 색감·사이즈 등 구매 고려 요소를 참고할 수 있습니다. '
    ranked = summary.get('ranked_evidence') or {}
    rank_note = ''
    if ranked:
        shapes = [x for x in ranked.get('related_shapes', [])
                  if x.get('products') and x.get('label') in ('후드형', '푸퍼형', '봄버형', '크롭형', '칼라형')]
        shape_text = ', '.join(f"{x['label']} {x['products']}개" for x in shapes[:4]) or '세부 디자인 분류 확인 필요'
        rank_note = (f"무신사 상위 랭킹의 여성·공용 비교 범위에서 '{subtype}' 관련 잠정 상품 "
                     f"{ranked.get('related_products', 0)}개를 확인했어요. 디자인 표현은 {shape_text} 등이며 "
                     '매출·선호도 순위가 아닙니다.\n'
                     f"해당 상품 리뷰 {ranked.get('related_reviews', 0)}건의 관심 표현은 "
                     '긍정·부정이 섞일 수 있으므로 H&M 성능 증명으로 사용하지 않습니다.\n\n')
    designs = summary.get('verified_designs') or []
    if designs:
        detail_text = ', '.join(f"{x['label']} {x['products']}개" for x in designs[:5])
        rank_note += f'H&M 확정 상품에서 직접 확인된 형태·디테일: {detail_text}. 해당 상품에만 활용합니다.\n\n'
    # 시장에서 관찰한 디자인과 확정 상품에서 검증한 디자인을 분리해 최종 문장에 반영한다.
    market_shapes = [x['label'] for x in ranked.get('related_shapes', [])
                     if x.get('products') and x.get('label') in ('후드형', '푸퍼형', '크롭형', '봄버형', '칼라형')]
    product_shapes = [x['label'] for x in designs
                      if x.get('matched_ids') and x.get('label') in ('스탠드칼라', '셔츠형 칼라', '봄버형', '후드형')]
    if market_shapes and product_shapes:
        final_note = (f"무신사 상위 랭킹의 관련 상품에서 {'·'.join(market_shapes[:3])} 디자인이 관찰됐고, "
                      f"선정된 H&M 상품에는 {'·'.join(product_shapes[:3])} 형태가 실제로 확인돼요. "
                      f"이를 참고해 {color_text} 색상 비교를 공통 콘텐츠로 구성하면서, "
                      '칼라·실루엣 등 확인된 차이를 해당 상품별로 보여주는 것을 추천합니다. '
                      '무신사 디자인의 랭킹 노출을 판매량·선호도로 해석하지 않고, '
                      'H&M에 확인되지 않은 형태나 보온 성능·착용감은 광고에 사용하지 않습니다.')
    elif product_shapes:
        final_note = (f"{color_text} 색상 비교를 기본으로, "
                      f"H&M 상품에서 확인된 {'·'.join(product_shapes[:3])} 등 디자인 차이를 "
                      '해당 상품 카드에만 안내합니다. 보온 성능·착용감은 추가 검증 전까지 제외합니다.')
    else:
        final_note = (f'{color_text}의 색상 비교와 상품별 디자인 안내를 기획전 공통 콘텐츠 포인트로 사용하고, '
                      '확인되지 않은 성능 표현은 제외합니다.')
    intro = (
        f'■ {subtype} 기획전 소구점 최종 정리\n'
        + market_text
        + '이 전체 리뷰 집계는 해당 소분류만의 리뷰가 아닙니다. 선정한 H&M 상품의 직접 후기도 아닙니다.\n\n'
        + rank_note
        + f'선택 소구점: {" / ".join(points)}\n\n'
        f'**기획전에 적용할 때**\n{color_text} 상품을 비교하고, '
        '상품 카드마다 확인된 디자인·디테일을 구분해 안내하는 방향을 추천합니다. '
        '실제 착용 사진이나 사이즈표는 확보한 경우에만 추가합니다.\n\n'
        '**확인할 점**\n실측 핏·보온 성능·착용감·현재 가격·할인율은 별도 검증 전에는 광고에 사용하지 않습니다.\n\n'
        f'**현재 정리:** {final_note}'
    )
    return intro


def approval_brief(summary, selected, campaign, market=None):
    """기존 JSON 승인 요청을 사람이 읽을 수 있는 선택 결과로 대체한다."""
    data = _selected(selected)
    theme = data.get('theme') or '기획전'
    points = data.get('points') or []
    return (selection_brief(summary, data, campaign, market)
            + f'\n\n기획전 주제: {theme}\n'
            + '가격·할인·기간: 확정값 없으면 미정(광고에 사용하지 않음)\n'
            + '\n이 구성으로 대표 문구 3안을 만들까요?')


def generate_candidates(summary, campaign):
    """선정 색상과 검증된 공통 소구점에 기반한 3가지 표현 방향을 생성."""
    values = campaign.get('values') or {}
    selected = _selected(values.get('selling_points'))
    points = selected.get('points') or []
    available = {c['label'] for c in summary.get('candidates') or []}
    if len(points) < 2 or not set(points).issubset(available):
        raise ValueError('확정된 공통 소구점이 12단계 근거와 일치하지 않습니다.')
    ids = summary.get('ids') or []
    if not ids or set(ids) != set(re.findall(r'(?<!\d)\d{10}(?!\d)', str(values.get('products') or ''))):
        raise ValueError('확정 상품이 12단계 근거와 다릅니다.')
    subtype = str(values.get('subtype') or values.get('category') or '').strip()
    colors = _colors(campaign)
    if not subtype or not colors:
        raise ValueError('확정 소분류 또는 색상 정보가 없습니다.')
    text_colors = '·'.join(colors)
    titles = [
        f'{text_colors}, 취향에 맞게 고르는 {subtype}',
        f'디자인의 차이를 살펴보는 {subtype} 컬렉션',
        f'색상은 취향대로, 디자인은 꼼꼼하게 — {subtype} 컬렉션',
    ]
    descriptions = [
        f'{text_colors} 색상별 상품을 한눈에 비교하는 구성입니다.',
        '상품별 여밈·칼라·포켓 등 확인된 디자인 설명을 카드에서 비교합니다.',
        '색상 선택과 상품별 디자인 차이 안내를 함께 강조합니다.',
    ]
    # 12단계에서 검증된 실제 상품의 형태 근거가 있으면 2안의 상품 카드 방향에 반영한다.
    # 무신사 상위 랭킹의 후드/푸퍼 형태를 H&M 모든 상품의 특징으로 주장하지 않는다.
    verified_designs = [x for x in (summary.get('verified_designs') or [])
                        if x.get('matched_ids') and x.get('label')]
    if verified_designs and '상품별 디자인 상세 비교' in points:
        features = '·'.join(x['label'] for x in verified_designs[:4])
        descriptions[1] = f'확정 상품 설명에서 확인한 {features} 등을 해당 상품 카드에서 비교합니다.'
        descriptions[2] = '색상별 선택과 확정 상품의 검증된 형태·디테일 비교를 함께 강조합니다.'
    candidates = [{'number': i + 1, 'headline': title, 'description': descriptions[i],
                   'basis': points[:], 'verified': True} for i, title in enumerate(titles)]
    # 상품별 카드의 자세한 기능 정보는 이 단계에서 추정하지 않는다.
    cards = [{'article_id': item, 'copy': f'{subtype} 상품 {item} — 색상·디자인 상세 확인'} for item in ids]
    return {'schema_version': 1, 'category': values.get('category'), 'subtype': subtype,
            'colors': colors, 'ids': ids, 'points': points, 'candidates': candidates, 'product_cards': cards,
            'status': 'draft'}


def validate_candidates(package, summary, campaign):
    """후보가 확정 입력으로 재현 가능한지 확인한다. 실패 시 16단계 진입 금지."""
    expected = generate_candidates(summary, campaign)
    if package != expected:
        raise ValueError('대표 문구 후보가 검증된 상품·소구점 근거와 일치하지 않습니다.')
    if len(package.get('candidates', [])) != 3:
        raise ValueError('대표 문구 후보는 3개여야 합니다.')
    return {**package, 'status': 'verified'}


def render_options(package):
    rows = ['■ 14~15단계 — 대표 문구 3안 (근거 검사 완료)',
            f"확정된 소구점: {', '.join(package['points'])}",
            '미확인 보온 성능·핏·가격·할인은 문구에 사용하지 않았어요.', '']
    for item in package['candidates']:
        rows += [f"{item['number']}안. {item['headline']}", f"  {item['description']}", '']
    rows += ['추천: 3안 — 색상 비교와 디자인 안내 두 소구점을 함께 반영합니다.',
             "어떤 문구로 진행할까요? 예: '3번으로 할게' 또는 '첫 번째가 좋아'",
             '선택 후 승인하면 기획전 페이지 틀에 적용할 수 있습니다.']
    return '\n'.join(rows)


def parse_copy_choice(message, package):
    """14~15단계 결과 중 하나만 자연어로 선택한다. 일반 질문은 승인으로 오인하지 않는다."""
    text = str(message or '').strip()
    if not text or any(x in text for x in ('?', '추천해', '비교해', '왜', '말고')):
        return None
    if not re.search(r'(?:할게|할래|갈게|고를게|선택|확정|좋아|좋겠어|으로|로\s*해)', text):
        return None
    hits = []
    for pattern, number in (
        (r'(?<!\d)[1](?:번|안|번째)(?!\d)|첫\s*번째|첫째', 1),
        (r'(?<!\d)[2](?:번|안|번째)(?!\d)|두\s*번째|둘째', 2),
        (r'(?<!\d)[3](?:번|안|번째)(?!\d)|세\s*번째|셋째|마지막', 3),
    ):
        if re.search(pattern, text):
            hits.append(number)
    if len(hits) != 1:
        return None
    number = hits[0]
    return next((c for c in (package.get('candidates') or []) if c.get('number') == number and c.get('verified')), None)


def selected_copy_value(choice, package):
    return json.dumps({'schema_version': 1, 'number': choice['number'],
                       'headline': choice['headline'], 'article_ids': package['ids'],
                       'subtype': package['subtype'], 'colors': package['colors']},
                      ensure_ascii=False, sort_keys=True)
