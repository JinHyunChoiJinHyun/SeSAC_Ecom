"""④ Content Generation Agent Tool 4개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    generate_campaign_copy      기획전 대표 문구 및 상품 카드 문구 생성
    build_campaign_page         기획전 HTML 페이지 틀 생성
    generate_segment_messages   고객 세그먼트별 맞춤 마케팅 메시지 생성
    plan_product_photography    리뷰·상품 정보 기반 필수 촬영 컷 및 추가 컷 제안 (서브 시나리오, 별도 촬영 Agent 없음)

아키텍처 [3]: 문구 작성은 Agent의 LLM, 저장·렌더링·해시는 백엔드
    문구 Tool은 두 번 씁니다: ① 인자 없이 호출 → 슬롯 규칙(GET /content/slot-rules)을 받음
                             ② LLM이 규칙대로 쓴 슬롯을 넣어 호출 → 초안 저장(POST /content/drafts) → content_id
    build_campaign_page          POST /content/{content_id}/render  (promotion_page.yaml 템플릿 → HTML)
    촬영 요청서 작성은 Reporting Agent(generate_photography_brief) 담당
로컬 모드: 미구현 (슬롯 규칙·템플릿·초안 저장은 백엔드 담당). 규칙 없이 쓴 문구를 결과로 내지 않습니다.
"""

from __future__ import annotations

from typing import Any

from agent_system import backend_client
from agent_system.tools.common import backend_or_unimplemented, clean_id, missing_id

SOURCE = "콘텐츠 초안 (백엔드 content_service)"
REASON = "백엔드 /content API(슬롯 규칙·템플릿·초안 저장) 연결 전"


def _rules_or_save(content_type: str, campaign_id: str, slots: dict | None) -> dict:
    if not slots:
        return backend_or_unimplemented("GET", "/content/slot-rules", source=SOURCE, reason=REASON,
                                        params={"content_type": content_type, "campaign_id": clean_id(campaign_id)})
    return backend_or_unimplemented("POST", "/content/drafts", source=SOURCE, reason=REASON, body={
        "campaign_id": clean_id(campaign_id), "content_type": content_type, "slots": slots})


def generate_campaign_copy(campaign_id: str, slots: dict[str, Any] | None = None) -> dict:
    """기획전 대표 문구 및 상품 카드 문구. slots 없이 부르면 슬롯 규칙을 받고, 규칙대로 쓴 slots를 넣어 부르면 초안 저장."""
    return _rules_or_save("page", campaign_id, slots)


def build_campaign_page(content_id: str) -> dict:
    """기획전 HTML 페이지 틀 생성 (저장된 문구 초안 content_id를 템플릿에 채워 렌더링)."""
    content_id = clean_id(content_id)
    if not content_id and (blocked := missing_id(SOURCE, "content_id")):
        return blocked
    return backend_or_unimplemented("POST", f"/content/{content_id}/render", source=SOURCE, reason=REASON)


def generate_segment_messages(campaign_id: str, messages: dict[str, Any] | None = None) -> dict:
    """세그먼트별 맞춤 메시지. messages 없이 부르면 슬롯 규칙, {세그먼트: 문구}를 넣으면 초안 저장."""
    return _rules_or_save("message", campaign_id, messages)


def plan_product_photography(campaign_id: str, plan: dict[str, Any] | None = None) -> dict:
    """리뷰·상품 정보 기반 필수 컷·추가 컷 제안. plan 없이 부르면 작성 규칙, 컷 목록을 넣으면 초안 저장."""
    return _rules_or_save("photography_plan", campaign_id, plan)


TOOLS = {
    "generate_campaign_copy": generate_campaign_copy,
    "build_campaign_page": build_campaign_page,
    "generate_segment_messages": generate_segment_messages,
    "plan_product_photography": plan_product_photography,
}
TOOL_SOURCE = {name: "content" for name in TOOLS}


def data_status() -> dict:
    return {"content": "백엔드 API로 저장·렌더링" if backend_client.enabled() else f"미구현 ({REASON})"}
