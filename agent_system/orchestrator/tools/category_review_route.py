"""상품군 상세 분석 요청을 진행 중인 기획전 워크플로로만 연결한다."""
from __future__ import annotations
import re

# 사용자 명령/상품 유형 요청/일반 SQL 분석과 혼동하지 않도록 요청 전체를 검사한다.
_PREFIX = r"(?:이번에는\s*|다음은\s*|다른\s*|상품군\s*|카테고리\s*)?"
_SUFFIX = r"(?:\s*(?:상품군|카테고리))?\s*(?:분석|비교|검토)(?:해\s*줘|해\s*주세요|해주세요|해줘|해봐|하자|해볼까)?"

def category_review_request(question: str) -> str | None:
    """5개 상품군 중 명시적 단일 상품군 분석 요청이면 표준 상품군 이름 반환."""
    from agent_system.orchestrator.tools.category_planning.review_selection import (
        parse_category_review, CATEGORIES,
    )
    text = str(question or '').strip().rstrip(' .!?~')
    if not text or re.search(r'(?:상품\s*유형|주력\s*상품|세부\s*유형|소분류|색상|스타일|승인|확정|취소|SQL)', text, re.I):
        return None
    # 일반 데이터 조회 문장을 임의로 기획전 워크플로로 전환하지 않는다.
    if not re.fullmatch(_PREFIX + r".+?" + _SUFFIX, text):
        return None
    category = parse_category_review(text)
    if category in CATEGORIES:
        return category
    return None

def workflow_category_ready(run: dict) -> bool:
    """현재 분석 세션의 진행 중인 상품군 검토 단계인지 검증."""
    return (run.get('workflow') == 'trend_planning'
            and run.get('status') in ('waiting_input', 'blocked', 'paused')
            and run.get('waiting_key') in ('category', 'category_confirmation', 'category_decision'))

def category_route_notice(category: str, run: dict) -> str:
    """상태가 없거나 단계가 다른 경우 잘못된 자유 SQL 실행 대신 안전하게 안내."""
    if run.get('workflow') != 'trend_planning' or run.get('status') in ('cancelled', 'done'):
        return (f'{category} 상품군 상세 분석을 위한 진행 중인 기획전 분석 세션이 없습니다. '
                '기존 세션의 상품군 비교 결과가 있는 동일 Slack 대화에서 요청하거나, '
                '새 분석 시작 후 기획전 조건부터 진행해 주세요. 다른 상품군 SQL은 실행하지 않았습니다.')
    return (f'{category} 상품군 상세 분석 요청을 받았지만 현재 워크플로는 상품군 검토 단계가 아닙니다. '
            f"현재 상태: {run.get('status') or '미상'} / 대기 항목: {run.get('waiting_key') or '없음'}. "
            '진행 상황을 확인한 뒤 해당 단계에서 요청해 주세요. 임의 SQL은 실행하지 않았습니다.')
