"""기획전 운영 날짜와 과거 월 단위 분석 범위를 분리한다."""
from calendar import monthrange
from datetime import date, timedelta


def _date(value):
    return value if type(value) is date else date.fromisoformat(value)


def _month(value, offset=0):
    year, month = divmod(value.year * 12 + value.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def _month_end(value):
    return date(value.year, value.month, monthrange(value.year, value.month)[1])


def build_period_plan(start, end, first_date, last_date, analysis_as_of,
                      buffer_months=1, *, after_months=0):
    """핵심은 시작~종료 월 전체, 사전은 이전 월 전체, 사후는 선택 사항."""
    start, end, first, last, as_of = map(_date, (start, end, first_date, last_date, analysis_as_of))
    last = min(last, as_of)
    if start > end or first > last:
        raise ValueError('기획전 또는 데이터 기간이 올바르지 않습니다.')
    span = (end.year-start.year)*12 + end.month-start.month+1
    if not 1 <= span <= 12:
        raise ValueError('핵심 분석 기간은 최대 12개월입니다.')
    for n in (buffer_months, after_months):
        if type(n) is not int or not 0 <= n <= 12:
            raise ValueError('보조 기간은 0~12개월 정수여야 합니다.')
    if span + buffer_months + after_months > 12:
        raise ValueError('핵심 및 보조 합계는 12개월 이하여야 합니다.')
    included, excluded = [], []
    for year in range(first.year-(end.year-start.year), min(last.year, start.year-1)+1):
        a = date(year, start.month, 1)
        b = _month_end(_month(a, span-1))
        if a < first or b > last:
            excluded.append({'start': a.isoformat(), 'end': b.isoformat(), 'reason': 'outside_observation_range'})
            continue
        def interval(lo, hi):
            return {'start': lo.isoformat(), 'end': hi.isoformat(), 'days': (hi-lo).days+1,
                    'within_bounds': first <= lo <= hi <= last}
        included.append({'core': interval(a,b),
                         'before': interval(_month(a,-buffer_months), a-timedelta(days=1)) if buffer_months else None,
                         'after': interval(b+timedelta(days=1), _month_end(_month(b,after_months))) if after_months else None})
    if not included:
        raise ValueError('완전히 관측된 과거 동일 기획전 월이 없습니다.')
    return {'campaign_start': start.isoformat(), 'campaign_end': end.isoformat(),
            'observed_start': first.isoformat(), 'observed_end': last.isoformat(),
            'analysis_as_of': as_of.isoformat(), 'granularity': 'month',
            'buffer_months': buffer_months, 'after_months': after_months,
            'seasons': included, 'excluded': excluded}


def validate_observed_days(plan, observed_days):
    """필터 전 거래 날짜로 완전성을 확인한다. 미관측일을 0건으로 처리하지 않는다."""
    observed = {_date(x) for x in observed_days}
    for season in plan['seasons']:
        for key in ('core','before','after'):
            row = season[key]
            if row is None:
                continue
            start, end = _date(row['start']), _date(row['end'])
            row['observed_days'] = sum(start+timedelta(days=i) in observed for i in range(row['days']))
            row['complete'] = row['within_bounds'] and row['observed_days'] == row['days']
        if not season['core']['complete']:
            raise ValueError('핵심 분석 월에 관측되지 않은 날짜가 있습니다. 추천을 중단합니다.')
    return plan


def period_sql(plan, column='t.t_dat', *, scope='core'):
    """핵심/사전/사후를 별도 조회한다. 불완전한 보조 기간은 비교에 사용하지 않는다."""
    if column != 't.t_dat' or scope not in ('core','before','after'):
        raise ValueError('허용되지 않은 조회 조건입니다.')
    if plan.get('granularity') != 'month':
        raise ValueError('월 단위 기간을 다시 계산하세요.')
    rows = [s[scope] for s in plan['seasons']]
    if not rows or any(r is None or r.get('complete') is not True for r in rows):
        raise ValueError('요청한 기간의 관측일 검증이 완료되지 않았습니다.')
    intervals = sorted((_date(r['start']), _date(r['end'])) for r in rows)
    first, last, cutoff = map(_date, (plan['observed_start'],plan['observed_end'],plan['analysis_as_of']))
    if any(a > b or a < first or b > min(last,cutoff) or a.day != 1 or b != _month_end(b) for a,b in intervals):
        raise ValueError('월 단위 기간 또는 기준일이 올바르지 않습니다.')
    if any(a <= prev_b for (_,prev_b),(a,_) in zip(intervals,intervals[1:])):
        raise ValueError('분석 기간이 중복됩니다.')
    return '('+' OR '.join(f'({column} BETWEEN ? AND ?)' for _ in intervals)+')', [d.isoformat() for pair in intervals for d in pair]


def core_sql(plan, column='t.t_dat'):
    return period_sql(plan, column)
