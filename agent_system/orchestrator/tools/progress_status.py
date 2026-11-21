"""Slack 진행 상태 전달. 활성 Agent만 실행 시점에 표시합니다."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

_reporter: ContextVar[Callable[[str], None] | None] = ContextVar("cart_signal_progress_reporter", default=None)

# 등록은 완료하지만 enabled=False Agent는 실제 실행되기 전에는 표시되지 않음.
AGENT_PROGRESS = {
    "general_analysis": "📊 데이터 조회 및 분석 중...",
    "market_analysis": "🔎 시장 트렌드와 경쟁 상품 분석 중...",
    "product_analysis": "🛍️ 상품 검색 및 판매 실적 비교 중...",
    # 아래 Agent들은 registry.py에서 enabled=True가 되어 실행될 때 자동으로 사용됨.
    "targeting": "🎯 고객 구매 가능성 예측 및 타깃 선정 중...",  # 예정
    "content_generation": "✍️ 기획전 문구와 콘텐츠 생성 중...",  # 예정
    "validation": "🔍 데이터 근거 및 마케팅 표현 검증 중...",  # 예정
    "reporting": "📄 분석 결과 정리 및 보고서 작성 중...",  # 예정: 전문 Agent 실행 시
    "campaign_execution": "📤 캠페인 승인 상태 및 실행 조건 확인 중...",  # 예정
}

@contextmanager
def progress_scope(reporter: Callable[[str], None] | None) -> Iterator[None]:
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)

def emit_progress(text: str) -> None:
    reporter = _reporter.get()
    if reporter is not None:
        try:
            reporter(text)
        except Exception:
            # Slack 상태 표시 오류로 본 분석 실패를 일으키지 않음.
            pass

def agent_progress(name: str) -> str:
    return AGENT_PROGRESS.get(name, "⚙️ 전문 Agent 처리 중...")
