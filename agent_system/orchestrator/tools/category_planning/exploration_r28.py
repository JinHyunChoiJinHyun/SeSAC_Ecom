"""R28: 승인 전 세부 유형 상세 분석 요청을 선택 문장과 분리하는 순수 파서."""
from __future__ import annotations
import re


def requested_subtype_review(message: str, options: list[dict]) -> str | None:
    """현재 승인 상품군의 실제 후보 이름과 일치하는 분석 명령만 허용."""
    raw = str(message or '').strip().rstrip(' .!?~')
    normalized = re.sub(r'\s+', '', raw)
    for option in options:
        name = str(option.get('keyword') or '')
        if not name:
            continue
        prefix = re.escape(re.sub(r'\s+', '', name))
        # 유형/색상 선택 및 부정/비교 명령은 상세 분석으로 오인하지 않는다.
        if re.fullmatch(prefix + r'(?:에대해)?(?:상세|색상별|색상|세부)?(?:분석|비교|구매추이)(?:다시)?(?:해줘|해주세요|해|보여줘|보여주세요)', normalized):
            return name
        if re.fullmatch(prefix + r'(?:에대해)?(?:상세|색상별|색상|세부)?(?:분석|비교)(?:결과)?(?:다시)?보여줘', normalized):
            return name
    return None


def render_subtype_review(option: dict) -> str:
    """저장된 검증 근거만으로 유형별 상세 설명을 구성한다."""
    subtype = str(option['keyword'])
    count = int(option.get('purchases') or 0)
    growth = option.get('growth')
    naver = (f'네이버 상대 관심도 기간 변화: {growth:+.1%}' if isinstance(growth, (int, float))
             else '네이버 상대 관심도 변화: 비교 근거 미확보')
    market = option.get('musinsa_evidence') or {}
    if market.get('status') == 'observed':
        musinsa = (f"무신사 관련 상품명 잠정 후보 {int(market.get('related_products') or 0)}개 / "
                   f"연결 리뷰 {int(market.get('related_reviews') or 0)}건 (판매량 아님)")
        if subtype == '경량패딩':
            musinsa += f" / 경량 표현 명시 {int(market.get('explicit_lightweight') or 0)}개"
    else:
        musinsa = '무신사 유형별 연결 근거 미확보'
    return (f'■ {subtype} 상세 분석 (미선택·미승인)\n'
            f'H&M {option.get("period_label") or "과거 핵심 월 온라인 구매"}: {count:,}건\n'
            f'{naver}\n{musinsa}\n'
            '색상별 구매 그래프와 표를 함께 제공합니다. 다른 유형도 계속 분석할 수 있습니다.\n'
            '확정할 때만 유형 1개와 색상 1~3개를 함께 말씀하세요. '
            '예: 경량패딩 블랙이랑 그린카키로 할게.\n'
            '과거 구매·검색 관심도·무신사 표본은 서로 다른 지표이며 현재 판매량을 뜻하지 않습니다.')
