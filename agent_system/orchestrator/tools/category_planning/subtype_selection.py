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
    # 유형 규칙은 상호 배타적이지 않아 한 상품이 여러 유형에 포함될 수 있다.
    # 이 집계는 중복 제거된 카테고리 전체 구매 수치로 합산하면 안 된다.
    for key in PATTERNS:
        rows = purchase_candidates(articles, purchases, category, subtype=key)
        if rows.empty:
            continue
        valid_colors = rows['colour_group_name'].fillna('').astype(str).str.strip()
        known = ~valid_colors.str.casefold().isin({'', 'unknown', 'undefined', 'nan', 'none'})
        filtered = rows.loc[known].copy()
        filtered['colour_group_name'] = valid_colors.loc[known]
        if filtered.empty:
            continue
        summary = (filtered.groupby('colour_group_name', sort=True)
                   .agg(purchases=('winter_online_purchases', 'sum'),
                        article_count=('article_id', 'nunique'))
                   .reset_index())
        summary = summary.sort_values(['purchases', 'colour_group_name'], ascending=[False, True], kind='stable')
        # 색상 미상 상품 구매는 유형 전체에서 제외하지 않고 별도 기록한다.
        total = int(rows.winter_online_purchases.sum())
        colors = [{'name': str(row.colour_group_name), 'purchases': int(row.purchases),
                   'article_count': int(row.article_count),
                   'purchase_share_percent': round(100 * int(row.purchases) / total, 2)}
                  for row in summary.itertuples(index=False)]
        inventory[key] = {'purchases': total, 'article_count': int(rows.article_id.nunique()),
                          'unknown_color_purchases': total - int(filtered.winter_online_purchases.sum()),
                          'colors': colors, 'overlap_possible': True}
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


def collect_musinsa_subtype_evidence(category, subtype_names):
    """무신사 로컬 표본에서 유형별 잠정 상품·리뷰 근거를 읽는다.

    판매량·인기 점수를 산출하지 않으며 실패/미수집은 명시적으로 기록한다.
    """
    from agent_system.orchestrator.tools.musinsa_ranked_evidence import load_ranked_evidence
    result = {}
    for key in subtype_names:
        try:
            info = load_ranked_evidence(category, key)
            result[key] = {
                'status': 'observed',
                'related_products': int(info['related_products']),
                'explicit_lightweight': int(info.get('lightweight_named_products') or 0),
                'related_reviews': int(info['related_reviews']),
                'eligible_products': int(info['eligible_products']),
                'gender_scope': str(info['gender_scope']),
                'basis': str(info['basis']),
            }
        except (FileNotFoundError, ValueError, KeyError, OSError) as exc:
            # 외부 데이터 오류를 H&M 구매나 네이버 관심도 0으로 처리하지 않는다.
            result[key] = {'status': 'unavailable', 'reason': type(exc).__name__}
    return result


def _musinsa_support(keyword, evidence):
    """경량패딩은 폭넓은 패딩/다운 후보가 아닌 명시적 경량 표현만 보조 근거로 취급."""
    if not isinstance(evidence, dict) or evidence.get('status') != 'observed':
        return False
    if canonical(keyword) == '경량패딩':
        return int(evidence.get('explicit_lightweight') or 0) > 0
    return int(evidence.get('related_products') or 0) > 0


def review_options(category, metrics, inventory, musinsa_evidence=None):
    """네이버/H&M과 무신사 유형별 경쟁 표본을 서로 다른 단위로 결합한다.

    네이버 상승 후보를 우선하되 동급 안에서는 검증 가능한 무신사
    상품명 참고 근거가 있는 후보를 먼저 표시한다. 판매량으로 환산하지 않는다.
    """
    market = musinsa_evidence or {}
    primary = rank_options(category, metrics, inventory)
    for option in primary:
        key = canonical(option['keyword'])
        evidence = market.get(key) or {}
        supported = _musinsa_support(key, evidence)
        option['musinsa_evidence'] = evidence
        option['musinsa_support'] = supported
        option['evidence_level'] = 'naver_hm_musinsa' if supported else 'naver_hm'
        option['ranking_scope'] = '네이버 상승 후보: 무신사 교차 참고 근거 확보 우선, 이후 상승률 순'
    primary.sort(key=lambda o: (not o['musinsa_support'], -o['growth'], o['keyword']))
    used = {canonical(o['keyword']) for o in primary}
    reference = sorted(
        ((key, info) for key, info in inventory.items() if key not in used),
        key=lambda x: (not _musinsa_support(x[0], market.get(x[0])),
                       -int(x[1].get('purchases', 0)), x[0]),
    )
    for key, info in reference:
        if not info.get('colors') or not info.get('purchases'):
            continue
        evidence = market.get(key) or {}
        supported = _musinsa_support(key, evidence)
        primary.append({'keyword': key, 'growth': None,
                        'evidence_level': 'hm_musinsa' if supported else 'hm_only',
                        'ranking_scope': 'H&M 참고 후보: 무신사 유형 근거 확보 우선, 이후 과거 구매량 순',
                        'musinsa_evidence': evidence,
                        'musinsa_support': supported, **info})
    return primary


def build_options(category, metrics, articles, purchases):
    return rank_options(category, metrics, build_inventory(category, articles, purchases))


def color_guide(option):
    return f"선택 유형: {option['keyword']}\n색상별 {option.get('period_label', '과거 겨울 온라인 구매')}: " + ', '.join(f"{c['name']} {c['purchases']:,}건" for c in option['colors']) + '\n색상은 1~3개 선택하세요. 예: 추천 색상으로 할게'


def guide(options):
    if not options:
        return '추천 보류: H&M 과거 구매 기록으로 검증된 세부 유형 후보가 없습니다. 데이터 또는 다른 카테고리를 확인하세요.'
    lines = ['■ 세부 유형 추천 및 선택 후보',
             '① 네이버 상승 근거 + H&M 구매 검증 후보 (무신사 교차 근거 확보 우선·이후 상승률 순)',
             '② 네이버 상승 근거 미확보 · H&M 구매 참고 후보 (무신사 근거 확보 우선·이후 구매 건수 순)',
             '※ 무신사 표본의 상품명·리뷰는 경쟁 시장 참고 근거일 뿐 판매량·긍정 평가·시장 점유율이 아닙니다.',
             '※ 두 그룹은 같은 시장 인기 순위가 아닙니다. 유형은 상품 설명 규칙 기반이며 중복될 수 있습니다.',
             '※ 한 상품이 경량패딩·패딩·재킷 등 여러 유형에 포함될 수 있으므로 유형별 구매 건수를 더해 전체 매출로 해석하지 않습니다.']
    if options[0].get('period_description'):
        lines.append('H&M 핵심 분석 월: ' + options[0]['period_description'])
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
            lines.append(f"{rank}. {o['keyword']} / {g} / H&M {o.get('period_label', '과거 겨울 온라인 구매')} {o['purchases']:,}건")
            market = o.get('musinsa_evidence') or {}
            if market.get('status') == 'observed':
                related = int(market.get('related_products') or 0)
                reviews = int(market.get('related_reviews') or 0)
                if canonical(o['keyword']) == '경량패딩':
                    explicit = int(market.get('explicit_lightweight') or 0)
                    lines.append(f"   무신사 경쟁 참고: 관련 상품명 잠정 후보 {related}개 중 경량 표현 명시 {explicit}개 / 연결 리뷰 {reviews}건")
                    lines.append('   ※ 패딩·다운 관련 후보 전부를 경량패딩으로 확정하지 않습니다.')
                else:
                    lines.append(f"   무신사 경쟁 참고: 유형 관련 상품명 잠정 후보 {related}개 / 연결 리뷰 {reviews}건")
                if not o.get('musinsa_support'):
                    lines.append('   무신사 유형별 교차 근거 미확보 (상품 관심 부족을 뜻하지 않음)')
            else:
                lines.append('   무신사 유형별 비교 자료 미확보 (상품 관심 부족을 뜻하지 않음)')
            if o.get("core_days"):
                note = f"   핵심 일평균 {o['purchases']/o['core_days']:,.1f}건"
                if o.get("before_purchases") is not None and o.get("before_days"):
                    note += f" / 사전 {o['before_purchases']:,}건 (일평균 {o['before_purchases']/o['before_days']:,.1f}건)"
                else:
                    note += " / 사전 비교 불가"
                lines.append(note)
            if o.get('recent_window'):
                lines.append(f"   비교 기간: {o.get('prior_window')} → {o['recent_window']}")
            lines.append('   색상별 과거 구매(상품 수): '+', '.join(
                f"{c['name']} {c['purchases']:,}건" +
                (f" · 상품 {c['article_count']:,}개" if c.get('article_count') is not None else '')
                for c in o['colors'][:5]))
            if o.get('unknown_color_purchases'):
                lines.append(f"   색상 미상 구매: {o['unknown_color_purchases']:,}건 (색상 추천에서 제외)")
    top = primary[0] if primary else None
    if top:
        if top.get('musinsa_support'):
            lines.append(f"추천: {top['keyword']} — 네이버 관심도·H&M 구매·무신사 관련 상품명 근거를 각각 확인한 우선 검토 대상입니다.")
        else:
            lines.append(f"추천: {top['keyword']} — 네이버 관심도·H&M 구매 근거를 확보했으나 무신사 유형별 교차 근거는 확인되지 않았습니다.")
    else:
        lines.append('추천 보류: 시장·내부 근거를 함께 충족한 유형이 없어 H&M 참고 후보만 제공합니다.')
    lines.append('선택: 소분류 하나와 색상 1~3개를 채팅으로 입력하세요. 예: 경량패딩 블랙, 그린 카키')
    lines.append('주의: 구매 기록은 과거 20~39세·' + ('핵심 월' if options[0].get('period_description') else '겨울') + '·온라인·여성용 상품 기준이며 현재 매출·재고를 보장하지 않습니다.')
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
