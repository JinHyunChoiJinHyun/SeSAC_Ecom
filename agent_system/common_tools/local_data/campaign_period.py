"""읽기 전용 H&M DB에서 공통 기간 조건을 확정한다."""
from shared_analysis.product.campaign_period import build_period_plan, validate_observed_days, period_sql


def load_period_plan(start, end, buffer_months=1, *, after_months=0):
    from agent_system.common_tools.local_data import hm_products
    from common.db_client import get_analysis_connection
    cutoff = hm_products.data_as_of()
    conn = get_analysis_connection()
    try:
        observed = [row[0] for row in conn.execute(
            'SELECT DISTINCT t_dat FROM hm.transactions WHERE t_dat <= ? AND t_dat IS NOT NULL ORDER BY t_dat',
            [cutoff.isoformat()]).fetchall()]
    finally:
        conn.close()
    if not observed:
        raise ValueError('관측된 H&M 거래 날짜가 없습니다.')
    plan = build_period_plan(start, end, min(observed), max(observed), cutoff, buffer_months, after_months=after_months)
    return validate_observed_days(plan, observed)


def purchase_date_filter(cutoff, *, winter_only=True, period_plan=None, scope="core"):
    """공통 SQL 조건. 기존 호출은 계절 조건, 명시적 plan 사용 시 핵심 기간만."""
    params = [cutoff.isoformat()]
    sql = ' AND t.t_dat <= ?'
    if period_plan is not None:
        if period_plan.get('analysis_as_of') != params[0]:
            raise ValueError('분석 기준일이 변경되었습니다. 기간을 다시 계산하세요.')
        condition, bounds = period_sql(period_plan, scope=scope)
        return sql + ' AND ' + condition, params + bounds
    if scope != "core":
        raise ValueError("보조 기간에는 명시적인 기간 계획이 필요합니다.")
    if winter_only:
        sql += ' AND EXTRACT(MONTH FROM t.t_dat) IN (12, 1, 2)'
    return sql, params
