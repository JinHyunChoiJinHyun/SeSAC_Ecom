"""승인 버튼은 반드시 발행 당시의 활성 분석 세션에서만 실행한다."""
from __future__ import annotations

def session_match(button_thread_id: str, current_thread_id: str) -> bool:
    """다른 세션·이전 승인 버튼을 거절한다. 빈 세션 ID도 허용하지 않는다."""
    return bool(button_thread_id and current_thread_id and button_thread_id == current_thread_id)
