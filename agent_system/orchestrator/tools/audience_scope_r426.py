"""R42.6: 사용자 연령 조건 → 과거 H&M 고객 모집단. 실제 고객 발송 없음."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
import re

# 여러 연령대를 하나의 연속 구간으로 임의 합치지 않는다.
_AGE = re.compile(r'(?<!\d)(?:(?P<lo>\d{1,3})\s*세?\s*[~～–-]\s*(?P<hi>\d{1,3})\s*세|(?P<d1>[1-9]0)\s*대?\s*(?:부터|[~～–·,/-]|와|및|과)\s*(?P<d2>[1-9]0)\s*대(?:까지)?|(?P<short>2030)\s*대|(?P<single>[1-9]0)\s*대|(?P<exact>\d{1,3})\s*세)(?!\d)')
_BAD = re.compile(r'제외|미만|이상|이하|초과|아닌|아니라|말고|이외|미성년')


def normalize(scope):
    if not isinstance(scope, dict) or set(scope) != {'age_min','age_max'}:
        raise ValueError('고객 연령 조건이 명확하지 않습니다. 예: 20~30대 또는 25~34세')
    lo, hi = scope['age_min'], scope['age_max']
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 120:
        raise ValueError('고객 연령 범위가 올바르지 않습니다.')
    return {'age_min':lo,'age_max':hi}


def parse_age(text):
    text = str(text or '')
    if re.search(r'전체\s*연령(?:대)?|전\s*연령(?:대)?', text):
        if _AGE.search(text) or _BAD.search(text):
            raise ValueError('전체 연령과 개별 연령 조건을 함께 지정할 수 없습니다.')
        return normalize({'age_min':0,'age_max':120})
    lower = re.search(r'(?<!\d)([1-9]0)\s*대\s*이상|(?<!\d)(\d{1,3})\s*세\s*이상', text)
    if lower:
        rest = text[:lower.start()] + text[lower.end():]
        if _AGE.search(rest) or _BAD.search(rest):
            raise ValueError('연령 하한을 하나만 지정해 주세요.')
        return normalize({'age_min':int(lower.group(1) or lower.group(2)), 'age_max':120})
    matches = list(_AGE.finditer(text))
    if not matches:
        raise ValueError('고객 연령 조건이 없습니다. 연령을 명시해 주세요.')
    # 부정·개방구간·불연속 범위는 오인하지 않는다.
    if len(matches) != 1 or _BAD.search(text):
        raise ValueError('고객 연령 조건을 하나의 명확한 연속 범위로 지정해 주세요.')
    m=matches[0]
    if m['lo'] is not None: lo,hi=int(m['lo']),int(m['hi'])
    elif m['d1'] is not None: lo,hi=int(m['d1']),int(m['d2'])+9
    elif m['short']: lo,hi=20,39
    elif m['single']: lo=int(m['single']); hi=lo+9
    else: lo=hi=int(m['exact'])
    return normalize({'age_min':lo,'age_max':hi})


def initial_scope(run):
    # 최초 원문에서만 복원한다. 이전 기획전 또는 LLM 추정값은 사용하지 않는다.
    try: return parse_age(run.get('request'))
    except ValueError: return None


def effective_scope(run):
    if 'target_audience' in run:
        try: return normalize(run['target_audience'])
        except ValueError: return None
    return initial_scope(run)


def require_scope(run):
    scope=effective_scope(run)
    if scope is None:
        raise ValueError('고객 연령 조건을 확인할 수 없습니다. 새 분석 요청에 20~30대처럼 연령을 명시해 주세요.')
    return scope


def label(scope):
    scope=normalize(scope)
    if scope == {'age_min':0,'age_max':120}: return '전체 유효 연령 (미확인 제외)'
    if scope['age_max']==120: return f"{scope['age_min']}세 이상 (유효 연령)"
    return f"{scope['age_min']}~{scope['age_max']}세" if scope['age_min']!=scope['age_max'] else f"{scope['age_min']}세"


def audience_text(target):
    scope=target.get('audience')
    if not scope: return '고객 연령: 미확인 (이전 분석)'
    return '고객 연령: '+label(scope)+' (H&M 저장 연령 기준)'


def population_text(target):
    counts=target.get('audience_counts')
    if not counts: return ''
    return (f"최근 90일 구매 고객 {counts['before_age']:,}명 → 연령 조건 충족 {counts['after_age']:,}명 "
            f"(범위 외 {counts['outside_age']:,}명 / 연령 미확인 {counts['unknown_age']:,}명 제외)")


def load_population(data, as_of, scope):
    """모델 _snapshot과 같은 직전 90일 상품 연결 구매 고객에서 저장 연령을 대조한다.

    원천의 연령은 구매 당시 또는 현재 연령으로 환산하지 않는다. 중복/충돌 연령은 미확인.
    모델의 구매 이력은 채널 제한 없는 기존 정의 그대로 유지한다.
    """
    import pandas as pd
    scope=normalize(scope)
    end=date.fromisoformat(str(as_of)); start=end-timedelta(days=90)
    query="""
      WITH eligible AS (
        SELECT DISTINCT t.customer_id
        FROM hm.transactions t JOIN hm.articles a ON t.article_id=a.article_id
        WHERE t.t_dat >= ? AND t.t_dat < ?
      ), ages AS (
        SELECT customer_id,
               CASE WHEN COUNT(*)=COUNT(age) AND MIN(age)=MAX(age)
                    THEN MIN(age) ELSE NULL END AS age
        FROM hm.customers GROUP BY customer_id
      )
      SELECT e.customer_id, a.age FROM eligible e
      LEFT JOIN ages a ON e.customer_id=a.customer_id ORDER BY e.customer_id
    """
    frame=data.con.execute(query,[start,end]).df()
    if frame.empty or frame.customer_id.isna().any() or frame.customer_id.astype(str).duplicated().any():
        raise ValueError('고객 모집단 조회 결과가 비었거나 고객 ID가 중복되었습니다.')
    age=pd.to_numeric(frame.age,errors='coerce')
    valid=age.notna() & age.between(0,120) & (age%1==0)
    allowed=valid & age.between(scope['age_min'],scope['age_max'])
    ids=frame.loc[allowed,'customer_id'].astype(str).tolist()
    counts={'before_age':len(frame),'after_age':len(ids),'outside_age':int((valid & ~allowed).sum()),'unknown_age':int((~valid).sum())}
    if not ids: raise ValueError('지정한 연령 조건을 충족하는 최근 90일 구매 고객이 없습니다.')
    return ids,counts


def valid_counts(counts, population):
    keys={'before_age','after_age','outside_age','unknown_age'}
    return (isinstance(counts,dict) and set(counts)==keys
            and all(type(x) is int and x>=0 for x in counts.values())
            and counts['after_age']==population and population>0
            and counts['before_age']==counts['after_age']+counts['outside_age']+counts['unknown_age'])


def current_target(target, campaign, run):
    from . import campaign_target_r39 as legacy
    if not isinstance(target, dict) or not target or target.get('fingerprint') != legacy.fingerprint(campaign, run):
        return False
    if target.get('policy') != 'r40_v1':
        # 연령이 지정된 기획전에서는 구형 무조건 타깃을 사용하지 않는다.
        return effective_scope(run) is None and not run.get('request')
    scope = effective_scope(run)
    if run.get('period_plan'):
        from .category_planning.analysis_scope_r427 import plan_scope
        try:
            if plan_scope(run['period_plan']) != scope: return False
        except ValueError: return False
    proposal = target.get('proposal')
    if proposal is not None and not isinstance(proposal, dict):
        return False
    p = target if target.get('status') == 'confirmed' else proposal or target
    combos = target.get('combos')
    if not isinstance(combos, list) or not combos or any(not isinstance(c, dict) for c in combos):
        return False
    # 검토 생성 전·후 및 확정 단계에서 동일한 연령 제한 모집단을 검증한다.
    pop = p.get('population', combos[0].get('total'))
    if type(pop) is not int or any(type(c.get('total')) is not int or c['total'] != pop for c in combos):
        return False
    counts = target.get('audience_counts')
    if not (scope and target.get('audience') == scope and valid_counts(counts, pop)):
        return False
    if proposal is not None and (proposal.get('population') != pop or proposal.get('audience') != scope
                                 or proposal.get('audience_counts') != counts):
        return False
    return True


def age_change_request(text):
    text=str(text or '')
    if re.search(r'메일|이메일|카피|문구|제목|본문',text): return False
    return bool(re.search(r'연령|나이|대상|고객|타[깃겟]',text) and
                re.search(r'변경|수정|바꿔|바꿀|다시|으로\s*할게|로\s*할게|으로만|대로만|으로\s*해줘|대로\s*해줘|로만',text) and
                (_AGE.search(text) or re.search(r'연령|나이',text)))


def target_only_change(text):
    return bool(re.search(r'고객\s*타[깃겟]|타[깃겟]\s*(?:연령|나이|고객|대상)|고객\s*(?:선정|분석)\s*(?:연령|나이|대상)',str(text)))


def reselection_request(text):
    return bool(re.fullmatch(r'\s*(?:고객\s*)?타[깃겟](?:을|를)?\s*(?:다시\s*선정|재선정)(?:해\s*줘|해주세요|해줘|하자|해보자)?[.!]?\s*',str(text)))


def reset_target(run,campaign,scope,step_index,*,start=False):
    """기획전·승인 페이지는 유지하고 타깃 이후 승인만 분리. 이전 파일은 삭제하지 않는다."""
    scope=normalize(scope)
    new=deepcopy(run); c=deepcopy(campaign or {})
    old=new.get('campaign_target_r39') or {}
    history=list(new.get('target_audience_history') or [])
    if old or new.get('report_sent_r39'):
        history.append({'audience':old.get('audience'),'fingerprint':old.get('fingerprint'),
                        'total':old.get('total'), 'report_sent':bool(new.get('report_sent_r39'))})
    new.update(target_audience=scope,audience_revision=int(new.get('audience_revision') or 0)+1,
               target_audience_history=history[-30:],campaign_target_r39={},segment_copy_r41={},report_draft={},
               status='running' if start else 'paused',waiting_key='',step_index=step_index,
               target_start_pending=not start)
    new.pop('report_sent_r39',None)
    vals=dict(c.get('values') or {})
    for key in ('target','report','page_url','first_screen'): vals.pop(key,None)
    c['values']=vals
    return new,c


def confirmation_request(text):
    return str(text).strip() in ('고객 타깃 재선정 승인', '고객 타깃 재선정 취소')


def prepare_reselection(run, campaign, scope, step_index, scorer, root):
    """새 결과 계산에 성공하기 전에는 기존 상태를 수정하지 않는다."""
    revised, updated = reset_target(run, campaign, scope, step_index, start=True)
    revised.pop('audience_reselect_pending', None)
    result = scorer(updated, revised, root=root)
    if not result or result.get('status') != 'review' or not current_target(result, updated, revised):
        raise ValueError('새 고객 선정 결과 검증에 실패했습니다.')
    revised['campaign_target_r39'] = result
    return revised, updated
