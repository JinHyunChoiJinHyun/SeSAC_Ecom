"""기획전 H&M 분석의 공통 연령 범위. 외부 표본에는 적용하지 않는다."""
from .. import audience_scope_r426 as audience

LEGACY = {'age_min':20, 'age_max':39}

def plan_scope(plan=None):
    return audience.normalize((plan or {}).get('analysis_audience', LEGACY))

def bind_plan(plan, scope):
    scope = audience.normalize(scope)
    if 'analysis_audience' in plan and plan_scope(plan) != scope:
        raise ValueError('기존 분석 연령과 다릅니다. 새 분석을 시작하세요.')
    return {**plan, 'analysis_audience':scope}

def run_scope(run):
    scope = audience.require_scope(run)
    if run.get('period_plan') and plan_scope(run['period_plan']) != scope:
        raise ValueError('시장·상품 분석 연령과 고객 조건이 다릅니다. 새 분석을 시작하세요.')
    if 'campaign_scope' in run or 'campaign_scope' in (run.get('period_plan') or {}):
        from ..campaign_scope_r43 import run_scope as campaign_scope
        campaign_scope(run)
    return scope

def report_label(run):
    plan = (run or {}).get('period_plan')
    if plan:
        return audience.label(plan_scope(plan))
    scope = audience.effective_scope(run or {})
    return audience.label(scope) if scope else '연령 미확인'

def query_args(plan=None):
    scope = plan_scope(plan)
    return [scope['age_min'],scope['age_max']]

EXTERNAL_NOTE = '네이버·무신사·유튜브는 수집된 시장 표본이며, 지정 연령대의 구매·리뷰·관심도로 확인된 자료가 아닙니다.'
