"""CART_SIGNAL 버튼을 특수 라우터보다 먼저 전달하는 안전한 디스패처.

실제 승인 요청의 토큰·상태·세션 검증은 기존 graph.run_turn 에서 수행합니다.
이 모듈은 승인 결정을 추론하거나 확정값을 직접 수정하지 않습니다.
"""
from __future__ import annotations
from typing import Callable

ALLOWED_BUTTON_TYPES = frozenset({
    "approval", "category_confirm", "subtype_suggestion",
    "copy_revision_review", "report_draft_review", "workflow", "page_preview_review", "target_selection_review", "segment_copy_review",
})

INVALID_BUTTON_REPLY = (
    "지원하지 않는 버튼 요청입니다. 최신 CART_SIGNAL 메시지에서 다시 선택해 주세요."
)

def dispatch_cart_signal_button(event: dict, graph_callback: Callable[[dict], dict]) -> dict:
    """버튼을 일반 자연어/승인자 지정 라우터에 전달하지 않습니다."""
    action = event.get("cart_signal_action")
    if not isinstance(action, dict) or action.get("type") not in ALLOWED_BUTTON_TYPES:
        return {"turn_type": "chitchat", "completed_steps": [], "final_answer": INVALID_BUTTON_REPLY}
    # 토큰·run ID 등 권한 검증은 graph가 수행하도록 값을 그대로 전달합니다.
    return graph_callback(action)
