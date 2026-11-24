"""H&M 2020-09-23 구매 모델을 이용한 고객 타깃 후보 순위 (실제 발송 불가)."""
from __future__ import annotations
import re
from pathlib import Path

MODEL_DATE = '2020-09-23'
ID_RE = re.compile(r'(?<!\d)\d{10}(?!\d)')


def resolve_selection(question: str, campaign: dict | None = None) -> dict:
    """정확한 상품 ID 또는 이미 명시된 품목/색상만 허용한다."""
    values = (campaign or {}).get('values') or {}
    if not isinstance(values, dict): values = {}
    ids = set(ID_RE.findall(question or ''))
    explicit = values.get('target_article_id')
    if explicit is not None: ids.add(str(explicit))
    if len(ids) > 1: raise ValueError('상품 ID가 여러 개입니다. 하나를 지정해 주세요.')
    if ids:
        item = next(iter(ids))
        if not ID_RE.fullmatch(item): raise ValueError('10자리 상품 ID가 필요합니다.')
        return {'article_id': item}
    pt, colour = values.get('target_product_type'), values.get('target_colour')
    if isinstance(pt,str) and pt.strip() and isinstance(colour,str) and colour.strip():
        return {'product_type': pt.strip(), 'colour': colour.strip()}
    return {}


def validate_ranked_frame(frame) -> None:
    """모델 출력값/순위 계약 검사."""
    import numpy as np
    required={'customer_id','score_logit','prob_uncalibrated','rank'}
    missing=required-set(frame.columns)
    if missing: raise ValueError('필수 점수 컬럼 누락: '+str(sorted(missing)))
    if frame.empty: raise ValueError('점수 대상 고객이 없습니다.')
    if frame.customer_id.isna().any() or frame.customer_id.astype(str).duplicated().any():
        raise ValueError('고객 ID 누락 또는 중복')
    logits=frame.score_logit.to_numpy(dtype=float)
    prob=frame.prob_uncalibrated.to_numpy(dtype=float)
    if (not np.isfinite(logits).all() or not np.isfinite(prob).all()
            or not ((prob>=0)&(prob<=1)).all()):
        raise ValueError('점수 NaN/Inf/범위 오류')
    if frame['rank'].tolist()!=list(range(1,len(frame)+1)):
        raise ValueError('순위가 1부터 연속되지 않습니다.')
    ids=frame.customer_id.astype(str).tolist()
    if any(logits[i]<logits[i+1] or (logits[i]==logits[i+1] and ids[i]>ids[i+1])
           for i in range(len(frame)-1)):
        raise ValueError('점수 정렬 또는 동점 순위 오류')


def summarize_model_ranked(frame) -> dict:
    """같은 품목×색상 안의 모델 순위 구간 인원만 집계한다 (구매 특성 세그먼트 아님)."""
    import math
    n = len(frame)
    if not n:
        raise ValueError('MODEL_RANK_EMPTY')
    cut10, cut30 = math.ceil(n * .10), math.ceil(n * .30)
    counts = [cut10, cut30 - cut10, n - cut30]
    names = ['모델 순위 상위 10%', '모델 순위 10~30%', '모델 순위 나머지 70%']
    return {'kind': 'model_rank_bands', 'total': n,
            'segments': [{'name': name, 'count': count,
                          'share_percent': round(count / n * 100, 2),
                          'basis': '동일 품목×색상 내 score_logit 순위'}
                         for name, count in zip(names, counts)],
            'basis': '점수화된 고객 전체의 같은 조합 내 순위 구간',
            'limits': '순위 구간은 구매 이력 특성 구간이 아니며 보정 구매확률·실제 발송 가능 인원을 의미하지 않음'}


def to_candidate_result(scored, limit: int = 100) -> dict:
    """기존 검증 게이트가 인식하는 rank_only 구조로 연결한다."""
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError('표 미리보기는 1~100행만 허용합니다.')
    validate_ranked_frame(scored.scores)
    frame=scored.scores.head(limit).copy()
    rows=[{'customer_id':str(r.customer_id),'score':float(r.prob_uncalibrated),
           'score_logit':float(r.score_logit),'rank':int(r.rank)}
          for r in frame.itertuples(index=False)]
    return {
        'final_answer': (
            f'[과거 데이터 기반 타깃 후보] {scored.product_type} × {scored.colour} / '
            f'기준일 {scored.score_as_of} / 30일 예측 / 점수 산출 {len(scored.scores):,}명 / '
            f'상위 {len(rows)}명 미리보기. 점수는 순위용이며 구매 확률은 보정되지 않았습니다. '
            '2026~2027년 실제 기획전 예측이 아닙니다. 수신 동의, 발송 상한, 제외 대상은 미확인입니다.'),
        'target_result': {
            'purpose':'rank_only', 'rows': rows,
            'product_type':scored.product_type, 'colour':scored.colour,
            'score_as_of':str(scored.score_as_of), 'model_as_of':str(scored.model_as_of),
            'model_run_id':scored.model_run_id,
            'total_scored_customers':len(scored.scores),
            'excluded_count':len(scored.excluded),
            'prediction_horizon_days':30,
            'score_interpretation':'uncalibrated_ranking_only',
            'segment_summary': summarize_model_ranked(scored.scores),
            'send_eligible_verified':False,
        },
        'dataframe':frame,
        'data_sources':['hm'],
        'data_purpose':'H&M 2020년 이력 기반 구매 순위 분석',
        'data_scope_note':'실제 캠페인 발송 대상이 아닌 과거 데이터 순위 분석',
    }


def calculate_target_candidates(selection: dict, repo_root: Path, limit: int=100,
                                score_as_of: str=MODEL_DATE) -> dict:
    """팀 H&M DuckDB 읽기 전용 연결 후 실제 저장 모델로 예측한다."""
    if score_as_of != MODEL_DATE: raise ValueError('현재 모델은 2020-09-23 하루만 지원합니다.')
    from modeling.purchase_model import connect_team, score_combo, score_article
    with connect_team(repo_root) as data:
        if 'article_id' in selection:
            scored=score_article(data,selection['article_id'],score_as_of)
        else:
            scored=score_combo(data,selection['product_type'],selection['colour'],score_as_of)
    return to_candidate_result(scored,limit=limit)


def run(request) -> dict:
    """Orchestrator AgentRequest 진입점. 명시적 발송 요청은 무조건 차단한다."""
    question=getattr(request,'user_question','') or getattr(request,'question','')
    if re.search(r'발송\s*(?:대상|명단|고객|확정)|발송할|실제\s*발송',question):
        return {'not_ready':True,'error':'TARGET_DELIVERY_NOT_READY',
                'final_answer':'발송 대상 확정은 아직 지원하지 않습니다. 수신 동의/제외/상한/대조군 검증 후 별도 승인해야 합니다.'}
    try:
        from agent_system.agents.dynamic_targeting import calculate_requested_target
        return calculate_requested_target(question, getattr(request, 'campaign', {}),
                                          Path(__file__).resolve().parents[2])
    except Exception as exc:
        # 미지원 분류와 모델/DB 실패를 0점으로 대체하거나 다른 품목으로 변경하지 않는다.
        return {'not_ready': True, 'error': 'TARGET_SELECTION_OR_MODEL_UNAVAILABLE',
                'final_answer': ('타깃 후보 조회 불가 (' + type(exc).__name__ + '). '
                                 '요청한 상품·카테고리, H&M 분류, 모델 지원 범위를 확인해 주세요.')}
