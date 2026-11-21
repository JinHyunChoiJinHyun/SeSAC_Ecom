"""세부 유형별 근거 및 선택. 추천 값과 유형 매핑 사전을 구분한다."""
import math
import re
import pandas as pd

ALIASES = {'경량 패딩':'경량패딩', '일반 패딩':'패딩', '자켓':'재킷'}
# 상품 설명에서 명시된 형태만 매칭. down the front 자체는 충전재가 아니다.
PATTERNS = {
    '경량패딩': r'\b(?:light[ -]?weight|lightly padded|thinly padded|light padded)\b',
    '패딩': r'\b(?:padded|puffer|down[ -]filled|filled with down)\b',
    '코트': r'\bcoat\b',
    '플리스': r'\bfleece\b',
    '재킷': r'\bjacket\b',
}
COLOR_ALIASES = {'블랙':'Black','검정':'Black','라이트베이지':'Light Beige','밝은베이지':'Light Beige','다크베이지':'Dark Beige','화이트':'White','네이비':'Dark Blue','그레이':'Grey','회색':'Grey','라이트블루':'Light Blue','그린카키':'Greenish Khaki','카키':'Greenish Khaki','베이지':'Beige','다크블루':'Dark Blue','옐로위시브라운':'Yellowish Brown','노란빛브라운':'Yellowish Brown'}


def canonical(keyword):
    return ALIASES.get(str(keyword).strip(), str(keyword).strip())


def subtype_mask(frame, keyword):
    key = canonical(keyword)
    text = frame['prod_name'].fillna('').astype(str) + ' ' + frame['detail_desc'].fillna('').astype(str)
    pattern = PATTERNS.get(key)
    if not pattern:
        return pd.Series(False, index=frame.index)
    match = text.str.contains(pattern, case=False, regex=True)
    if key == '경량패딩':
        match &= text.str.contains(PATTERNS['패딩'], case=False, regex=True)
        match &= ~text.str.contains(r'\b(?:heavyweight|heavily padded|thickly padded)\b', case=False, regex=True)
    return match


def ranked_keywords(metrics):
    def eligible(item):
        k, v = item
        g = v.get('recent_growth')
        return isinstance(g, (int,float)) and math.isfinite(g) and g >= .1
    return sorted((k for k,v in metrics.items() if eligible((k,v))), key=lambda k: (-metrics[k]['recent_growth'], k))


def build_inventory(category, articles, purchases):
    """5단계 상품 조회 결과를 유형별 구매량·색상 집계로 축소한다."""
    from agent_system.orchestrator.tools.category_planning.verified_products import purchase_candidates
    inventory = {}
    for key in PATTERNS:
        rows = purchase_candidates(articles, purchases, category, subtype=key)
        if rows.empty:
            continue
        counts = rows.groupby('colour_group_name')['winter_online_purchases'].sum().sort_index().sort_values(ascending=False, kind='stable')
        colors = [{'name':str(k),'purchases':int(v)} for k,v in counts.items() if str(k).strip() and str(k).lower() not in {'unknown','undefined','nan','none'}]
        if colors:
            inventory[key] = {'purchases':int(rows.winter_online_purchases.sum()), 'colors':colors}
    return inventory


def rank_options(category, metrics, inventory):
    """7단계에서는 저장된 내부 집계와 외부 상승 신호만 결합한다."""
    options, seen = [], set()
    for keyword in ranked_keywords(metrics):
        key = canonical(keyword)
        if metrics[keyword].get('category', category) != category or key not in inventory or key in seen:
            continue
        seen.add(key)
        m = metrics[keyword]
        options.append({'keyword':keyword,'growth':m['recent_growth'], 'latest_period':m.get('latest_period'), 'prior_window':m.get('prior_window'), 'recent_window':m.get('recent_window'), **inventory[key]})
    return options


def review_options(category, metrics, inventory):
    """시장 근거 검증 순위 + H&M 과거 구매만 있는 참고 후보를 분리한다.

    네이버 미수집 후보는 상승률을 0으로 간주하거나 '시장 추천'으로 표현하지 않는다.
    """
    primary = rank_options(category, metrics, inventory)
    for option in primary:
        option['evidence_level'] = 'naver_hm'
    used = {canonical(o['keyword']) for o in primary}
    reference = sorted(
        ((key, info) for key, info in inventory.items() if key not in used),
        key=lambda x: (-int(x[1].get('purchases', 0)), x[0]),
    )
    for key, info in reference:
        if not info.get('colors') or not info.get('purchases'):
            continue
        primary.append({'keyword': key, 'growth': None, 'evidence_level': 'hm_only', **info})
    return primary


def build_options(category, metrics, articles, purchases):
    return rank_options(category, metrics, build_inventory(category, articles, purchases))


def color_guide(option):
    return f"선택 유형: {option['keyword']}\n색상별 과거 겨울 온라인 구매: " + ', '.join(f"{c['name']} {c['purchases']:,}건" for c in option['colors']) + '\n색상은 1~3개 선택하세요. 예: 추천 색상으로 할게'


def guide(options):
    if not options:
        return '추천 보류: H&M 과거 구매 기록으로 검증된 세부 유형 후보가 없습니다. 데이터 또는 다른 카테고리를 확인하세요.'
    lines = ['■ 세부 유형 추천 및 선택 후보',
             '① 네이버 상승 근거 + H&M 구매 검증 후보 (상승률 기준 순위)',
             '② 네이버 상승 근거 미확보 · H&M 구매 기록만 있는 참고 후보 (구매 건수 기준)',
             '※ 두 그룹은 같은 시장 인기 순위가 아닙니다. 유형은 상품 설명 규칙 기반이며 중복될 수 있습니다.']
    primary = [o for o in options if o.get('growth') is not None]
    reference = [o for o in options if o.get('growth') is None]
    for group, group_options in [('시장·내부 근거 검증', primary), ('내부 구매 참고', reference)]:
        if not group_options:
            lines.append(f'{group}: 해당 후보 없음')
            continue
        lines.append(f'[{group}]')
        for o in group_options:
            rank = options.index(o) + 1
            g = (f"네이버 상대 관심도 변화 {o['growth']:+.1%}" if o.get('growth') is not None
                 else '네이버 상승 근거 없음/미수집')
            lines.append(f"{rank}. {o['keyword']} / {g} / H&M 과거 겨울 온라인 구매 {o['purchases']:,}건")
            if o.get('recent_window'):
                lines.append(f"   비교 기간: {o.get('prior_window')} → {o['recent_window']}")
            lines.append('   색상별 구매: '+', '.join(f"{c['name']} {c['purchases']:,}건" for c in o['colors'][:5]))
    top = primary[0] if primary else None
    if top:
        lines.append(f"추천: {top['keyword']} — 시장·내부 근거를 함께 충족한 후보 중 우선 검토 대상입니다.")
    else:
        lines.append('추천 보류: 시장·내부 근거를 함께 충족한 유형이 없어 H&M 참고 후보만 제공합니다.')
    lines.append('선택: 소분류 하나와 색상 1~3개를 채팅으로 입력하세요. 예: 경량패딩 블랙, 그린 카키')
    lines.append('주의: 구매 기록은 과거 20~39세·겨울·온라인·여성용 상품 기준이며 현재 매출·재고를 보장하지 않습니다.')
    return '\n'.join(lines)


def parse_selection(text, options):
    """명시한 유형과 색상만 저장. 질문 또는 모호한 색상은 확정하지 않는다."""
    s = str(text).strip()
    if not options or re.search(r'말고|제외|아니|취소|하지\s*마|하지\s*않',s):
        return None
    chosen = None
    selection_tail = ''
    m = re.search(r'유형\s*:\s*(\d+)',s)
    if m:
        n=int(m.group(1)); chosen=options[n-1] if 1<=n<=len(options) else None
    else:
        matches = []
        for o in options:
            statement = re.search(
                r'(?<![가-힣A-Za-z])' + re.escape(o['keyword'])
                + r'\s*(?:으로|로)?\s*(?:갈게|할게|결정할게|선택할게|확정할게|하고|,|，|(?=\s+(?:블랙|검정|그린|카키|베이지|다크|네이비|화이트)))(?=[.!\s,，]|$)',
                s,
            )
            if statement:
                matches.append((o, statement.end()))
        if len(matches) == 1:
            chosen, pos = matches[0]
            selection_tail = s[pos:]
        elif re.search(r'추천\s*(?:유형|상품)(?:으로|로)\s*(?:갈게|할게)', s):
            chosen = options[0]
    if not chosen: return None
    available = [c['name'] for c in chosen['colors']]
    if re.search(r'추천\s*색상(?:으?로|대로)\s*(?:할게|갈게|선택할게|확정할게)(?:[.! ]|$)',s): colors=available[:2]
    else:
        m = re.search(r'색상\s*:\s*([^\n]+)',s)
        if m:
            values = [v.strip().rstrip('.') for v in m.group(1).split(',')]
            colors = [COLOR_ALIASES.get(v.replace(' ',''),v) for v in values]
        else:
            # 명시적으로 선택한 세부 유형 뒤에 나온 색상만 인식한다.
            tail = selection_tail.strip() if selection_tail else ''
            tail = re.sub(r'^[.!,，\s]+', '', tail)
            tail = re.split(r'\s*(?:[.!]\s*)?(?:그러면|그럼)\s+', tail, maxsplit=1)[0].strip()
            if not tail or re.match(r'^(?:어떤|무슨)\s*색상', tail):
                colors = []
            else:
                # 문장 끝의 선택 표현을 먼저 제거한다. '색으로 정할게'도 허용한다.
                tail = re.sub(r'\s*(?:색상|색)?\s*(?:으로|로)?\s*(?:정할게|고를게|선택할게|확정할게|할게|갈게)[.! ]*$', '', tail).strip()
                tail = re.sub(r'^색상(?:은|을|이)?\s*', '', tail)
                tail = re.sub(r'(?:으로|로)[.!\s]*$', '', tail).strip()
                values = re.split(r'\s*(?:,|，|이랑|랑|하고|그리고|및)\s*', tail)
                colors=[]
                for v in values:
                    v=v.strip().rstrip('.!')
                    exact=next((a for a in available if a.casefold()==v.casefold()),None)
                    value=COLOR_ALIASES.get(v.replace(' ',''),exact)
                    if value is None: raise ValueError('색상을 정확히 지정하거나 추천 색상으로 선택해 주세요.')
                    colors.append(value)
    colors = list(dict.fromkeys(colors))
    if len(colors) > 3: raise ValueError("색상은 1~3개까지만 선택할 수 있습니다.")
    if any(c not in available for c in colors): raise ValueError('선택한 색상에 검증된 구매 상품이 없습니다.')
    return {'subtype':chosen['keyword'],'colors':colors}
