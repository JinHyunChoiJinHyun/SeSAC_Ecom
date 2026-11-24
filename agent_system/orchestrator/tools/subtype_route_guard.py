"""확정 상품군의 유형 분석을 일반 자유 SQL과 분리하는 상태 검증 유틸리티."""
from __future__ import annotations
from datetime import datetime, timezone
import re

_SUBTYPE = re.compile(r"(?:주력\s*상품\s*유형|상품\s*유형|세부\s*유형|상품\s*종류|소분류)")
_ANALYSIS = re.compile(r"(?:분석|비교|추천|구매|근거|트렌드|추세|보여)")

def is_subtype_analysis_request(question: str) -> bool:
    """선택·승인·취소와 혼동하지 않는 명시적 상품 유형 분석 요청."""
    text = str(question or '').strip()
    if re.search(r'(?:취소|반려|승인|확정|로\s*할게|으로\s*갈게)', text) and not re.search(r'확정된\s*.+상품군', text):
        return False
    return bool(_SUBTYPE.search(text) and _ANALYSIS.search(text))

def matches_current_approval(run: dict, campaign: dict) -> bool:
    """직전 워크플로 시작 후 기록된 같은 상품군의 승인만 복원 대상으로 인정."""
    if run.get('workflow') != 'trend_planning' or run.get('status') != 'waiting_input':
        return False
    if run.get('waiting_key') not in ('category_confirmation', 'category_decision'):
        return False
    category = str(run.get('review_category') or '')
    if not category or (campaign.get('values') or {}).get('category') != category:
        return False
    started = str(run.get('started_at') or '')
    if not started:
        return False
    try:
        start_dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    for item in reversed(campaign.get('log') or []):
        if item.get('key') != 'category' or item.get('value') != category:
            continue
        try:
            approved_dt = datetime.fromisoformat(str(item.get('at') or '').replace('Z', '+00:00'))
            if approved_dt.tzinfo is None:
                approved_dt = approved_dt.replace(tzinfo=timezone.utc)
            return approved_dt >= start_dt
        except (ValueError, TypeError):
            return False
    return False

def confirm_category_run(run: dict, category: str) -> dict | None:
    """승인값은 현재 검토 상품군과 일치할 때만 워크플로에 반영."""
    if (run.get('workflow') != 'trend_planning' or run.get('status') != 'waiting_input'
            or run.get('waiting_key') not in ('category_confirmation', 'category_decision')
            or run.get('review_category') != category):
        return None
    revised = dict(run, category_confirmed=True, category_decision_token='')
    if revised.get('subtype_flow') and revised.get('subtype_options'):
        revised.update(status='waiting_input', waiting_key='subtype_colors')
    else:
        # 유형 후보가 없다면 승인만 기록하고 일반 SQL로 넘기지 않는다.
        revised.update(status='waiting_input', waiting_key='subtype_evidence_missing')
    return revised

def subtype_status_reply(run: dict) -> str:
    category = str(run.get('review_category') or '선택 상품군')
    if run.get('waiting_key') == 'subtype_colors' and run.get('subtype_options'):
        from agent_system.orchestrator.tools.category_planning.subtype_selection import guide
        return (f'■ 확정 상품군: {category}\n'
                '주력 상품 유형 후보 비교 (현재 워크플로에 저장된 H&M 구매·네이버·무신사 근거)\n'
                + guide(run['subtype_options']) + '\n\n상품 유형을 먼저 선택하세요. 색상은 그다음에 검토합니다.')
    if run.get('waiting_key') == 'subtype_evidence_missing':
        return (f'{category} 상품군은 승인됐지만 유형별 검증 후보가 없습니다. '
                '임의 SQL 조회로 대체하지 않습니다. 유형별 근거 재분석이 필요합니다.')
    return (f'{category} 상품군의 확정 승인 상태가 현재 워크플로에 확인되지 않았습니다. '
            '상품군 선택과 승인 상태를 먼저 확인하세요. 다른 상품군의 SQL을 실행하지 않습니다.')
