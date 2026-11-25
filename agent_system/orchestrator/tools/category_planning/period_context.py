"""Run 단위 운영 일정/과거 구매 기간 전달 및 표시 (V4.17B)."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import re

_DATE = r'(?:\d{4}-\d{1,2}-\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)'
_RANGE = re.compile(rf'(?P<a>{_DATE})\s*(?:~|～|–|—|부터|에서|to)\s*(?P<b>{_DATE})\s*(?:까지)?', re.I)


def _as_date(text):
    return date(*map(int, re.findall(r'\d+', text)))


def parse_period(text):
    """연도 포함 명시적 날짜 범위만 확정 입력으로 인정한다."""
    text = str(text or '')
    if re.search(r'정도|쯤|대략|예시|또는|혹은|말고|아니|\?', text):
        return None
    matches = list(_RANGE.finditer(text))
    if len(matches) != 1 or len(re.findall(_DATE, text)) != 2:
        return None
    try:
        start, end = (_as_date(matches[0][key]) for key in ('a', 'b'))
    except ValueError as exc:
        raise ValueError('유효한 운영 날짜를 입력해 주세요.') from exc
    if start > end:
        raise ValueError('종료일은 시작일 이후여야 합니다.')
    span = (end.year-start.year)*12 + end.month-start.month+1
    if span > 11:
        raise ValueError('사전 1개월을 포함해 최대 12개월까지 분석할 수 있습니다.')
    return {'start': start.isoformat(), 'end': end.isoformat()}


# 날짜가 모호한 경우 가능한 날짜 전체를 계산하고 월 범위가 동일할 때만 진행한다.
_NUM = r'(?:[0-9]+|한|두|세|네)'
_DURATION = re.compile(rf'(?P<n>{_NUM})\s*(?P<unit>개월|달|주|일)(?:\s*(?:동안|간))?(?!\s*(?:뒤|후|전|월|일|년|부터|까지|~))')
_OFFSET = re.compile(r'(?P<n>[0-9]+)\s*(?P<unit>주|일)\s*(?:뒤|후)(?:에|부터)?')
_MONTH_START = re.compile(r'(?:(?P<year>[0-9]{4})\s*년\s*|(?P<nextyear>내년)\s*)?(?P<month>[0-9]{1,2})\s*월\s*(?:(?P<day>[0-9]{1,2})\s*일(?:\s*(?P<approx>정도|쯤|경))?|(?P<part>초순|중순|하순|초|중|말))?\s*(?:부터|에)?')
_ABSOLUTE_DAY = re.compile(r'(?P<year>[0-9]{4})[-/.](?P<month>[0-9]{1,2})[-/.](?P<day>[0-9]{1,2})\s*(?:부터|에)?')
_SHORT_RANGE = re.compile(r'(?:(?P<y>[0-9]{4})\s*(?:년\s*|[-/.])?)?(?P<m>[0-9]{1,2})\s*(?:월\s*|[-/.])(?P<d>[0-9]{1,2})\s*일?\s*(?:~|～|–|—|부터|에서)\s*(?:(?P<ey>[0-9]{4})\s*(?:년\s*|[-/.])?)?(?P<em>[0-9]{1,2})\s*(?:월\s*|[-/.])(?P<ed>[0-9]{1,2})\s*(?:일)?(?:까지)?')
_CONFLICT = re.compile(r'예시|또는|혹은|말고|아니|\?')


def _shift_months(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return date(year, month+1, min(day.day, monthrange(year, month+1)[1]))


def _duration_end(start, n, unit):
    if not 1 <= n <= {'일': 330, '주': 47, '달': 11, '개월': 11}[unit]:
        raise ValueError('운영 기간은 양수이며 사전 월을 포함해 최대 12개월이어야 합니다.')
    exclusive = _shift_months(start, n) if unit in ('달', '개월') else start + timedelta(days=n*(7 if unit == '주' else 1))
    return exclusive - timedelta(days=1)


def _schedule_result(pairs, precision, today):
    if not pairs:
        return None
    month_bounds = {(a.year, a.month, b.year, b.month) for a, b in pairs}
    if len(month_bounds) != 1:
        return None
    a, b = pairs[0]
    span = (b.year-a.year)*12 + b.month-a.month+1
    if a > b or not 1 <= span <= 11:
        raise ValueError('종료일과 분석 범위(사전 월 포함 최대 12개월)를 확인해 주세요.')
    # 추정 일정의 대표 날짜는 과거 월 계산용으로만 사용한다.
    result = {'dates': {'start': a.isoformat(), 'end': b.isoformat()}, 'precision': precision,
              'reference_date': today.isoformat()}
    if precision == 'month':
        result['label'] = f'{a.year}년 {a.month}월 ~ {b.year}년 {b.month}월 (운영 일자 미확정, 분석 월 기준)'
    elif precision == 'calculated':
        result['label'] = f'{a.isoformat()} ~ {b.isoformat()} (자동 계산 예정일, 운영 확정 전)'
    return result


def interpret_schedule(text, today=None):
    """지원하는 한국어 일정 표현을 실행 시점 KST 기준으로 해석한다.

    명시적 날짜는 그대로, 상대 날짜는 계산값, 범위 표현은 월 단위로 구분한다.
    여럿의 해석이 다른 월을 만들거나 상충하는 표현이 있으면 추가 확인한다.
    """
    text = str(text or '')
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    if _CONFLICT.search(text):
        return None
    # 명시 날짜 범위의 기존 엄격 검증과 호환한다.
    exact = parse_period(text)
    if exact:
        residual = _RANGE.sub('', text)
        if _OFFSET.search(residual) or _MONTH_START.search(residual) or _ABSOLUTE_DAY.search(residual) or _DURATION.search(residual):
            return None
        return _schedule_result([tuple(date.fromisoformat(exact[k]) for k in ('start','end'))], 'exact', today)
    ranges = list(_SHORT_RANGE.finditer(text))
    if ranges:
        if len(ranges) != 1 or re.search(r'정도|쯤|대략|경부터', text):
            return None
        m = ranges[0]
        # 다른 기간 표현이 같이 있으면 임의로 하나를 선택하지 않는다.
        residual = text[:m.start()] + text[m.end():]
        if _OFFSET.search(residual) or _MONTH_START.search(residual) or _ABSOLUTE_DAY.search(residual) or _DURATION.search(residual):
            return None
        month, day, end_month, end_day = map(int,(m['m'],m['d'],m['em'],m['ed']))
        year = int(m['y']) if m['y'] else today.year
        a = date(year, month, day)
        if not m['y'] and a < today:
            a = date(year+1, month, day)
        ey = int(m['ey']) if m['ey'] else a.year + int(end_month < month)
        b = date(ey,end_month,end_day)
        return _schedule_result([(a,b)], 'exact' if m['y'] and m['ey'] else 'calculated', today)
    candidates = []
    for m in _OFFSET.finditer(text):
        n = int(m['n'])
        if n > (52 if m['unit']=='주' else 366):
            raise ValueError('상대 시작일은 1년 이내로 입력해 주세요.')
        a = today + timedelta(days=n*(7 if m['unit']=='주' else 1))
        candidates.append((m, [a], 'calculated'))
    for m in re.finditer(r'다음\s*달(?:부터|에)?',text):
        candidates.append((m, [_shift_months(today.replace(day=1),1)], 'calculated'))
    for m in _ABSOLUTE_DAY.finditer(text):
        candidates.append((m, [date(int(m['year']),int(m['month']),int(m['day']))], 'calculated'))
    for m in _MONTH_START.finditer(text):
        month = int(m['month'])
        year = int(m['year']) if m['year'] else today.year + int(bool(m['nextyear']) or month < today.month)
        if not m['year'] and not m['nextyear'] and m['day'] and date(year,month,int(m['day'])) < today:
            year += 1
        last = monthrange(year,month)[1]
        part = m['part']
        if m['day']:
            day = int(m['day'])
            date(year,month,day)  # 날짜 유효성 검사
            if m['approx']:
                # '10일 정도'는 +/-3일을 가정하지 않고 해당 월 전체를 가능한 시작일로 본다.
                days = range(1,last+1)
                precision = 'month'
            else:
                days = [day]
                precision = 'calculated'
        elif part:
            days = range(1,11) if part in ('초','초순') else range(11,21) if part in ('중','중순') else range(21,last+1)
            precision = 'month'
        else:
            # '3월부터'는 월 시작이라는 명시적 관례를 사용한다.
            days = [1]
            precision = 'calculated'
        candidates.append((m,[date(year,month,d) for d in days],precision))
    if len(candidates) != 1:
        return None
    m, starts, precision = candidates[0]
    # 시작 표현 뒤의 기간만 받는다. 다른 기간을 잘못 집어오는 것을 방지한다.
    tail = text[m.end():]
    durations = list(_DURATION.finditer(tail))
    if len(durations) != 1 or re.search(r'정도|쯤|대략', tail) or re.search(r'대략|쯤', text[:m.start()]):
        return None
    duration = durations[0]
    if not re.fullmatch(r'\s*(?:에서)?\s*',tail[:duration.start()]):
        return None
    n = {'한':1,'두':2,'세':3,'네':4}.get(duration['n'])
    if n is None:
        n = int(duration['n'])
    return _schedule_result([(a,_duration_end(a,n,duration['unit'])) for a in starts], precision, today)


def schedule_text(period):
    return f"{period['start']} ~ {period['end']}" if period else '미확정'


def date_prompt(run):
    return ('분석 대상 월을 정하려면 기획전 시작 시점과 운영 기간을 알려 주세요. '
            '예: 3주 뒤부터 한 달간, 12월 중순부터 4주간, 2026-12-10 ~ 2027-01-09. '
            '해석에 따라 분석 월이 달라지는 경우에만 정확한 범위를 확인합니다.')


def execution_schedule_ready(plan):
    """추정 일정을 실제 발송 등 실행 단계의 확정 날짜로 사용하지 않는다."""
    return not plan or (plan.get('schedule_interpretation') or {}).get('precision', 'exact') == 'exact'


def plan_schedule(plan):
    interpretation = plan.get('schedule_interpretation') or {}
    return interpretation.get('label') or f"{plan['campaign_start']} ~ {plan['campaign_end']}"


def complete_scope(plan, scope):
    rows = [s.get(scope) for s in (plan or {}).get('seasons', [])]
    return bool(rows) and all(row and row.get('complete') is True for row in rows)


def scope_days(plan, scope):
    if not complete_scope(plan, scope):
        return None
    return sum(s[scope]['days'] for s in plan['seasons'])


def scope_text(plan, scope='core'):
    if not complete_scope(plan, scope):
        return '전체 대응 시즌의 관측 범위 미충족 — 비교 제외'
    return ', '.join(f"{s[scope]['start']}~{s[scope]['end']}" for s in plan['seasons']) + f' (총 {scope_days(plan, scope)}일)'


def period_note(plan):
    if not plan:
        return 'H&M 과거 겨울(12~2월) 구매 기록'
    return (f"운영 일정: {plan_schedule(plan)}\n"
            f"H&M 핵심 분석 월: {scope_text(plan)}\n"
            f"H&M 사전 분석 월: {scope_text(plan, 'before')}\n"
            f"H&M 분석 기준일: {plan['analysis_as_of']} / 과거 구매 기록이며 현재 판매량이 아닙니다. "
            '추천 정렬은 핵심 월 기준, 사전 월은 별도 참고입니다.')


def purchase_label(plan):
    return '과거 핵심 월 온라인 구매' if plan else '과거 겨울 온라인 구매'


def run_schedule(run, fallback='미확정'):
    plan = (run or {}).get('period_plan')
    return plan_schedule(plan) if plan else fallback


def period_table_note(plan, core, before):
    """사전 월을 핵심 월에 합산하지 않고 일평균을 병기한다."""
    core_days, before_days = scope_days(plan, 'core'), scope_days(plan, 'before')
    return (f'핵심 {core:,}건 (일평균 {core/core_days:,.1f}건) / '
            + (f'사전 {before:,}건 (일평균 {before/before_days:,.1f}건)' if before is not None and before_days else '사전 비교 불가'))


def build_period_product_result(category, plan, subtype_flow=False):
    """5단계: DB에서 검증된 기간만 조회한다. 제품 수와 거래 건수를 구분한다."""
    from collections import Counter
    import pandas as pd
    from .verified_products import load_purchase_inputs, purchase_candidates
    from .subtype_selection import build_inventory, PATTERNS, subtype_mask

    from .analysis_scope_r427 import plan_scope
    from ..audience_scope_r426 import label
    age = plan_scope(plan)
    age_label = label(age)
    articles, core = load_purchase_inputs(period_plan=plan, scope='core')
    selected = purchase_candidates(articles, core, category)
    core_total = int(selected['winter_online_purchases'].sum())
    before = None
    previous_inventory = {}
    if complete_scope(plan, 'before'):
        _, before_purchases = load_purchase_inputs(period_plan=plan, scope='before')
        before_rows = purchase_candidates(articles, before_purchases, category)
        before = int(before_rows['winter_online_purchases'].sum())
        if subtype_flow:
            # 알려진 색상이 없더라도 해당 유형 구매는 사전 합계에 포함한다.
            previous_inventory = {key: int(before_rows.loc[subtype_mask(before_rows, key), 'winter_online_purchases'].sum())
                                  for key in PATTERNS}
    inventory = build_inventory(category, articles, core) if subtype_flow else {}
    for key, value in inventory.items():
        value.update(analysis_age_label=age_label, period_label=purchase_label(plan), period_description=scope_text(plan),
                     before_purchases=previous_inventory.get(key, 0) if before is not None else None,
                     core_days=scope_days(plan, 'core'), before_days=scope_days(plan, 'before'))
    tags = Counter(tag for values in selected.get('style_tags', []) if isinstance(values, (list, tuple, set)) for tag in set(values))
    colors = selected['colour_group_name'].dropna().value_counts().to_dict()
    snapshot = {'basis': f'핵심 분석 월 구매 기록이 있는 여성용 상품 구성 ({age_label}·온라인)',
                'total_articles': int(selected['article_id'].nunique()),
                'total_products': int(selected['product_code'].nunique()),
                'colour_counts': {str(k): int(v) for k,v in colors.items()},
                'style_tag_counts': dict(tags)}
    summary = period_table_note(plan, core_total, before)
    return {'final_answer': f"{category} H&M 상품 분석 ({age_label})\n{period_note(plan)}\n{summary}\n"
                            f"핵심 월 거래 확인 상품 {snapshot['total_articles']:,}개 / 모델 {snapshot['total_products']:,}개. "
                            '주력 상품 유형 분석은 상품군 선택 이후 진행하며, 색상·스타일은 상품 유형 선택 후 분석합니다.',
            'dataframe': pd.DataFrame([{'category': category, 'core_purchases': core_total, 'before_purchases': before,
                                        'core_days': scope_days(plan, 'core'), 'before_days': scope_days(plan, 'before'),
                                        'core_purchases_per_day': round(core_total/scope_days(plan, 'core'), 2),
                                        'before_purchases_per_day': round(before/scope_days(plan, 'before'), 2) if before is not None else None}]),
            'product_result': {'search_products': snapshot}, 'subtype_inventory': inventory,
            'period_comparison': {'core_purchases': core_total, 'before_purchases': before}, 'data_sources': ['hm']}


def validate_report_context(run, campaign):
    """새 Run 보고서에 이전 Run의 승인값·기간이 섞이지 않게 한다."""
    if not run.get('period_required') and not run.get('period_plan'):
        return
    from agent_system.workflows.base import confirmed_since
    plan = run.get('period_plan')
    values = (campaign or {}).get('values') or {}
    if not plan or not complete_scope(plan, 'core'):
        raise ValueError('검증된 과거 분석 기간이 없습니다. 현재 Run의 분석을 먼저 완료하세요.')
    if 'analysis_audience' in plan:
        from .analysis_scope_r427 import run_scope
        age = run_scope(run)
        if any(r.get('analysis_age_min') != age['age_min'] or r.get('analysis_age_max') != age['age_max']
               for r in run.get('verified_candidate_rows') or []):
            raise ValueError('확정 상품의 분석 연령이 현재 기획전과 다릅니다. 새 분석을 시작하세요.')
    if not schedule_matches(run, values.get('schedule')):
        raise ValueError('승인된 운영 일정과 분석 기간이 일치하지 않습니다.')
    if any(not confirmed_since(campaign, key, run.get('started_at', '')) for key in ('products', 'schedule', 'page')):
        raise ValueError('현재 Run의 상품·일정·대표 문구를 승인한 후 보고서를 작성하세요.')
    ids = re.findall(r'(?<!\d)\d{10}(?!\d)', str(values.get('products') or ''))
    rows = {str(r.get('article_id')): r for r in run.get('verified_candidate_rows') or []}
    if not ids or any(rows.get(aid, {}).get('analysis_period') != scope_text(plan) for aid in ids):
        raise ValueError('확정 상품의 분석 기간이 현재 Run과 일치하지 않습니다.')


def schedule_matches(run, value):
    plan = (run or {}).get('period_plan') or {}
    if plan and not execution_schedule_ready(plan):
        # 예정 일정은 상품 승인/보고서 동일성 확인용으로만 저장한다.
        return value == plan_schedule(plan)
    try:
        parsed = parse_period(value)
    except ValueError:
        return False
    return parsed == {'start': plan.get('campaign_start'), 'end': plan.get('campaign_end')}


def saved_comparison_note(run):
    plan = (run or {}).get('period_plan')
    comparison = (run or {}).get('hm_period_comparison')
    if not plan or not isinstance(comparison, dict) or type(comparison.get('core_purchases')) is not int:
        return ''
    return ('선택한 상품군 전체 (과거 상품 사례별 구매 합계와 별도): '
            + period_table_note(plan, comparison['core_purchases'], comparison.get('before_purchases')))
