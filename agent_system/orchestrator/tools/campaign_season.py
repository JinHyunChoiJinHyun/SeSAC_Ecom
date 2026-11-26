"""기획전 콘텐츠 계절과 실제 분석 기간을 분리한다."""
from datetime import date, timedelta
import re

MONTH_SEASONS = ('겨울','겨울','봄','봄','봄','여름','여름','여름','가을','가을','가을','겨울')
ENGLISH = {'봄':'SPRING','여름':'SUMMER','가을':'AUTUMN','겨울':'WINTER'}


def explicit_seasons(text):
    """긍정적으로 명시한 계절만 추출하고 봄버 등의 상품명은 제외한다."""
    text = str(text or '')
    if re.search(r'또는|혹은|예시|비교', text):
        return []
    pattern = re.compile(r'(?<![가-힣A-Za-z])(?:(?:올해|올|초|한|늦)\s*)?(봄|여름|가을|겨울)(?=$|[^가-힣A-Za-z]|철|용|옷|의류|상품|기획전|시즌|부터|에|을|은|과|로|의|까지|맞이|느낌|분위기|컬렉션|스타일|이|가|만)')
    hits = []
    for match in pattern.finditer(text):
        tail = text[match.end():]
        if re.match(r'\s*(?:이|가|은|는|을|를)?\s*(?:말고|아닌|아니라|제외)', tail):
            continue
        hits.append(match.group(1))
    english = {'spring':'봄','summer':'여름','autumn':'가을','winter':'겨울'}
    for match in re.finditer(r'(?i)(?<![A-Za-z])(spring|summer|autumn|winter)(?![A-Za-z])',text):
        hits.append(english[match.group(1).lower()])
    return list(dict.fromkeys(hits))


def resolve_season(request='', dates=None, *, precision='exact', explicit=None):
    explicit = explicit_seasons(request) if explicit is None else list(dict.fromkeys(explicit))
    if any(s not in ENGLISH for s in explicit):
        raise ValueError('명시 계절이 올바르지 않습니다.')
    counts = {}
    months = []
    if dates:
        try:
            start, end = (date.fromisoformat(str(dates[k])) for k in ('start','end'))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('기획전 운영 기간 형식 오류') from exc
        if start > end or (end-start).days > 366:
            raise ValueError('기획전 운영 기간 범위 오류')
        if precision == 'month':
            from calendar import monthrange
            start = start.replace(day=1)
            end = end.replace(day=monthrange(end.year,end.month)[1])
        cursor = start
        while cursor <= end:
            season = MONTH_SEASONS[cursor.month-1]
            counts[season] = counts.get(season, 0)+1
            month = cursor.strftime('%Y-%m')
            if month not in months: months.append(month)
            cursor += timedelta(days=1)
    ranked = sorted(counts, key=lambda s:-counts[s])
    # 동률일 때는 두 계절을 함께 표기한다. 여러 계절 운영 정보는 별도 보존한다.
    selected = explicit or [s for s in ranked if counts[s]==counts[ranked[0]]]
    return {'season':'·'.join(selected) or '계절 중립',
            'season_en':' / '.join(ENGLISH[s] for s in selected) or 'COLLECTION',
            'source':'explicit' if explicit else 'period' if counts else 'neutral',
            'period_seasons':ranked,'season_days':counts,'campaign_months':months,
            'season_conflict':bool(explicit and ranked and not set(explicit).intersection(s for s in ranked if counts[s]==counts[ranked[0]])),
            'period_precision':precision if dates else None}


def from_campaign(run=None, campaign=None):
    run = run or {}; values = (campaign or {}).get('values') or {}
    plan = run.get('period_plan') or {}
    dates = run.get('campaign_dates')
    if plan.get('campaign_start') and plan.get('campaign_end'):
        dates = {'start':plan['campaign_start'],'end':plan['campaign_end']}
    precision = (plan.get('schedule_interpretation') or run.get('schedule_interpretation') or {}).get('precision','exact')
    if not dates:
        from .category_planning.period_context import parse_period
        dates = parse_period(values.get('schedule'))
    return resolve_season(run.get('request',''), dates, precision=precision, explicit=run.get('marketing_seasons'))


def season_words(context=None):
    season = (context or {}).get('season','계절 중립')
    if season == '계절 중립':
        return '일상', 'COLLECTION', '데일리 워드로브'
    return season, (context or {}).get('season_en','COLLECTION'), {'겨울':'윈터 워드로브','봄':'스프링 워드로브','여름':'서머 워드로브','가을':'가을 워드로브'}.get(season,f'{season} 워드로브')
