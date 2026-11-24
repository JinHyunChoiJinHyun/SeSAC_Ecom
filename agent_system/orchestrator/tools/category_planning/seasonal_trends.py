"""H&M 관측 월별 상품군 구매 기록. 예상값이나 네이버 지수와 결합하지 않는다."""
from __future__ import annotations
from datetime import date
import pandas as pd


def period_windows(plan):
    """기획전과 대응하는 과거 시즌의 관측 월만 조회한다."""
    if not plan:
        return []
    result = []
    for index, season in enumerate(plan.get('seasons', [])):
        for scope in ('before', 'core'):
            p = season.get(scope) or {}
            if not p.get('complete') or not p.get('start') or not p.get('end'):
                continue
            a, b = date.fromisoformat(str(p['start'])), date.fromisoformat(str(p['end']))
            if a > b:
                raise ValueError('분석 구간의 시작일이 종료일보다 늦습니다.')
            result.append({'season': index, 'scope': scope, 'start': a, 'end': b})
    return result


def aggregate_months(articles, transactions, windows, categories=None):
    """범위별 월 집계. 동일 상품이 여러 상품군에 귀속되면 오류로 처리한다."""
    from shared_analysis.product.category_mapping import CATEGORIES, category_masks
    categories = tuple(categories or CATEGORIES)
    if not windows or transactions is None or transactions.empty:
        return pd.DataFrame(columns=['season', 'scope', 'month', 'category', 'purchases'])
    eligible = articles.loc[articles['index_group_name'].isin(['Ladieswear', 'Divided'])].copy()
    eligible['article_id'] = eligible['article_id'].astype(str).str.zfill(10)
    masks = category_masks(eligible)
    category_frames = []
    for category in categories:
        selected = eligible.loc[masks[category], ['article_id']].copy()
        selected['category'] = category
        category_frames.append(selected)
    links = pd.concat(category_frames, ignore_index=True)
    if links['article_id'].duplicated().any():
        raise ValueError('한 H&M 상품이 여러 상품군에 중복 귀속되어 월별 집계를 중단합니다.')
    tx = transactions.copy()
    tx['article_id'] = tx['article_id'].astype(str).str.zfill(10)
    tx['month'] = tx['month'].astype(str).str.slice(0,7)
    tx['purchases'] = pd.to_numeric(tx['purchases'], errors='raise')
    if (tx['purchases'] < 0).any():
        raise ValueError('구매 건수가 음수입니다.')
    joined = tx.merge(links, how='inner', on='article_id', validate='many_to_one')
    if joined.empty:
        raise ValueError('H&M 월별 거래와 여성 상품군을 연결하지 못했습니다.')
    out = joined.groupby(['month', 'category'], as_index=False)['purchases'].sum()
    # 날짜 필터는 SQL에서 period_windows로 적용. 반올림하거나 없는 달을 지어내지 않는다.
    return out


def load_monthly_purchases(plan):
    """기존 H&M DB 연결 재사용. 결과는 실제 DB 조회값만 허용한다."""
    windows = period_windows(plan)
    if not windows:
        raise ValueError('월별 비교가 가능한 확정 관측 구간이 없습니다.')
    from common.db_client import get_analysis_connection
    from agent_system.common_tools.local_data import hm_products
    articles = hm_products.articles()
    if 'analysis_as_of' in plan and str(plan['analysis_as_of']) != str(hm_products.data_as_of()):
        raise ValueError('H&M 기준일이 변경돼 기간별 구매 추이 분석을 중단합니다.')
    clauses, args = [], []
    for p in windows:
        clauses.append('(CAST(t.t_dat AS DATE) >= ? AND CAST(t.t_dat AS DATE) <= ?)')
        args.extend([p['start'].isoformat(), p['end'].isoformat()])
    sql = ("SELECT SUBSTR(CAST(t.t_dat AS VARCHAR), 1, 7) AS month, "
           "CAST(t.article_id AS VARCHAR) AS article_id, COUNT(*) AS purchases "
           "FROM hm.transactions AS t "
           "JOIN hm.customers AS c ON c.customer_id = t.customer_id "
           "WHERE t.sales_channel_id = 2 AND c.age BETWEEN 20 AND 39 AND ("
           + ' OR '.join(clauses) + ") GROUP BY 1, 2")
    conn = get_analysis_connection()
    try:
        tx = conn.execute(sql, args).df()
    finally:
        conn.close()
    monthly = aggregate_months(articles, tx, windows)
    monthly['month_date'] = pd.to_datetime(monthly['month'] + '-01').dt.date
    records = []
    for window in windows:
        # 관측 월 단위 구간: 시작과 종료가 부분월이면 해당 월의 거래 중 입력 날짜에 포함되는 일만 읽힌다.
        a, b = window['start'], window['end']
        for row in monthly.itertuples(index=False):
            month_start = row.month_date
            if (month_start.year, month_start.month) < (a.year, a.month) or (month_start.year, month_start.month) > (b.year, b.month):
                continue
            records.append({'season': window['season'], 'scope': window['scope'],
                            'month': row.month, 'category': row.category, 'purchases': int(row.purchases)})
    # 각 시즌은 월 단위로 구성되므로 구간별 달은 서로 겹치지 않아야 한다.
    months_seen = set()
    for window in windows:
        months = pd.period_range(window['start'], window['end'], freq='M')
        for month in months:
            if str(month) in months_seen:
                raise ValueError('서로 다른 기간에 같은 월이 중복되어 있습니다.')
            months_seen.add(str(month))
    return add_observed_days(pd.DataFrame(records, columns=['season','scope','month','category','purchases']), plan)


def verify_monthly_totals(frame, category_rows):
    """표의 구매 실적과 시계열 합계가 다르면 그래프를 표시하지 않는다."""
    for row in category_rows:
        cat = row['category']
        part = frame.loc[frame['category'].eq(cat)]
        expected = row.get('hm_purchases')
        actual = int(part.loc[part['scope'].eq('core'), 'purchases'].sum())
        if expected is not None and actual != int(expected):
            raise ValueError(f'{cat} 핵심 월 구매 합계 불일치 (시계열={actual}, 추천표={expected})')
        before = row.get('hm_before_purchases')
        if before is not None:
            actual_before = int(part.loc[part['scope'].eq('before'), 'purchases'].sum())
            if actual_before != int(before):
                raise ValueError(f'{cat} 사전 월 구매 합계 불일치 (시계열={actual_before}, 추천표={before})')
    return True


def monthly_daily_average(frame):
    """월별 구매 건수의 일평균: 완전한 월은 달력 일수, 부분월은 실제 관측 일수."""
    from calendar import monthrange
    out=frame.copy()
    if out.empty:return out.assign(observed_days=pd.Series(dtype='int64'),purchases_per_day=pd.Series(dtype='float64'))
    if 'observed_days' not in out.columns:
        out['observed_days']=[monthrange(int(m[:4]),int(m[5:7]))[1] for m in out['month']]
    if (pd.to_numeric(out['observed_days']) <= 0).any():
        raise ValueError('관측 일수는 양수여야 합니다.')
    out['purchases_per_day']=out['purchases'].astype(float)/out['observed_days'].astype(float)
    return out


def add_observed_days(monthly, plan):
    """계획의 사전·핵심 구간에서 각 월별 실제 관측일 수를 기록한다."""
    from calendar import monthrange
    out=monthly.copy()
    windows=period_windows(plan)
    lookup={(x['season'],x['scope']):x for x in windows}
    days=[]
    for r in out.itertuples(index=False):
        w=lookup[(r.season,r.scope)]
        y,m=map(int,r.month.split('-'))
        first=date(y,m,1);last=date(y,m,monthrange(y,m)[1])
        count=(min(last,w['end'])-max(first,w['start'])).days+1
        if count <= 0:raise ValueError('관측 월과 분석 기간이 일치하지 않습니다.')
        days.append(count)
    out['observed_days']=days
    return monthly_daily_average(out)


def aligned_campaign_windows(plan):
    """사용자 일정의 월일을 과거 시즌에 대입하며, 검증되지 않은 일정을 추정하지 않는다."""
    if not plan or not plan.get('campaign_start') or not plan.get('campaign_end'):
        raise ValueError('기획전 시작일과 종료일이 없습니다.')
    campaign_start=date.fromisoformat(str(plan['campaign_start']))
    campaign_end=date.fromisoformat(str(plan['campaign_end']))
    if campaign_start>campaign_end:raise ValueError('기획전 종료일 오류')
    result=[]
    for i,season in enumerate(plan.get('seasons',[])):
        core=season.get('core') or {}
        if not core.get('complete'):raise ValueError('과거 핵심 기간이 완전하지 않습니다.')
        anchor=date.fromisoformat(str(core['start']))
        start_year=anchor.year
        start=date(start_year,campaign_start.month,campaign_start.day)
        end=date(start_year+(1 if (campaign_end.month,campaign_end.day)<(campaign_start.month,campaign_start.day) else 0),campaign_end.month,campaign_end.day)
        core_start=date.fromisoformat(str(core['start']))
        core_end=date.fromisoformat(str(core['end']))
        if not (core_start<=start<=end<=core_end):
            raise ValueError('기획전 대응 날짜가 검증된 핵심 분석 월을 벗어납니다.')
        result.append({'season':i,'start':start,'end':end,'start_year':start.year})
    return result


def load_aligned_weekly_purchases(plan, categories=None):
    """H&M 원본의 기획전 대응 기간을 주차별로 실제 조회한다. 판매량 추정은 하지 않는다."""
    from common.db_client import get_analysis_connection
    from agent_system.common_tools.local_data import hm_products
    from shared_analysis.product.category_mapping import CATEGORIES,category_masks
    windows=aligned_campaign_windows(plan)
    if not windows: raise ValueError('과거 기획전 대응 시즌이 없습니다.')
    if 'analysis_as_of' in plan and str(plan['analysis_as_of'])!=str(hm_products.data_as_of()):
        raise ValueError('H&M 데이터 기준일이 달라졌습니다.')
    articles=hm_products.articles()
    eligible=articles.loc[articles['index_group_name'].isin(['Ladieswear','Divided'])].copy()
    eligible['article_id']=eligible['article_id'].astype(str).str.zfill(10)
    cats=tuple(categories or CATEGORIES)
    masks=category_masks(eligible)
    links=pd.concat([eligible.loc[masks[c],['article_id']].assign(category=c) for c in cats],ignore_index=True)
    if links.article_id.duplicated().any():
        raise ValueError('H&M 상품군 분류가 중복되었습니다.')
    clauses=[];args=[]
    for w in windows:
        clauses.append('(CAST(t.t_dat AS DATE) BETWEEN ? AND ?)')
        args.extend([w['start'].isoformat(),w['end'].isoformat()])
    sql=('SELECT CAST(t.t_dat AS DATE) AS purchase_day, CAST(t.article_id AS VARCHAR) AS article_id, COUNT(*) AS purchases '
         'FROM hm.transactions t JOIN hm.customers c ON c.customer_id=t.customer_id '
         'WHERE t.sales_channel_id=2 AND c.age BETWEEN 20 AND 39 AND ('+' OR '.join(clauses)+') '
         'GROUP BY 1,2')
    conn=get_analysis_connection()
    try: tx=conn.execute(sql,args).df()
    finally:conn.close()
    if tx.empty:raise ValueError('대응 기획전 기간 구매 기록이 없습니다.')
    tx['article_id']=tx.article_id.astype(str).str.zfill(10)
    tx['purchase_day']=pd.to_datetime(tx.purchase_day).dt.date
    tx['purchases']=pd.to_numeric(tx.purchases,errors='raise')
    if (tx.purchases<0).any():raise ValueError('음수 구매 건수')
    joined=tx.merge(links,on='article_id',validate='many_to_one',how='inner')
    if joined.empty:raise ValueError('상품군 연결 거래가 없습니다.')
    rows=[]
    for w in windows:
        subset=joined.loc[(joined.purchase_day>=w['start'])&(joined.purchase_day<=w['end'])].copy()
        subset['week_index']=subset.purchase_day.map(lambda day:(day-w['start']).days//7+1)
        grouped=subset.groupby(['week_index','category'],as_index=False).purchases.sum()
        week_count=(w['end']-w['start']).days//7+1
        for cat in cats:
            vals=grouped.loc[grouped.category.eq(cat)].set_index('week_index').purchases.to_dict()
            for week_index in range(1,week_count+1):
                week_start=w['start']+pd.Timedelta(days=(week_index-1)*7)
                week_end=min(w['end'],week_start+pd.Timedelta(days=6))
                days=(week_end-week_start).days+1
                n=int(vals.get(week_index,0))
                rows.append({'season':w['season'],'start_year':w['start_year'],'category':cat,'week_index':week_index,
                             'week_start':str(week_start),'week_end':str(week_end),'observed_days':days,
                             'purchases':n,'purchases_per_day':n/days})
    return pd.DataFrame(rows)
