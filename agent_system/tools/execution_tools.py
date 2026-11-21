"""⑦ Campaign Execution Agent Tool 6개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    send_approval_email          팀장에게 보고서 링크와 승인 요청 메일 발송          [실행 Tool]
    track_approval_status        승인·수정 요청·반려 상태 관리 (조회)
    deliver_approved_page        승인된 HTML 파일 전달                              [실행 Tool]
    check_campaign_url           등록된 기획전 페이지 URL 접근 확인
    send_campaign_messages       확정된 타깃에게 메시지 발송                         [실행 Tool]
    schedule_campaign_followup   캠페인 발송 결과 및 사후 확인 예약                  [실행 Tool]

안전 규칙 (설계안: 명시적 승인 없이 자동 실행하지 않음, 아키텍처 원칙 4·6)
    - 실행 Tool(GUARDED_TOOLS)은 사람이 승인한 경우(AgentRequest.approved_actions)에만 base_agent가 호출합니다.
      채팅의 "승인할게"는 승인이 아닙니다. 승인 기록(approvals API)이 생기면 그 기록으로 approved_actions를 채웁니다.
    - 인자는 campaign_id만 받습니다 (수신자·내용을 Agent가 바꾸지 못하게). 최종 차단은 백엔드 게이트가 합니다.
백엔드 API (아키텍처 [3])
    POST /execution/{id}/approval-request   GET /execution/{id}/approval-status   POST /execution/{id}/page-delivery
    POST /execution/{id}/url-check          POST /execution/{id}/send             POST /execution/{id}/followups
로컬 모드: 미구현 (메일·발송·예약은 백엔드 담당).
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.tools.common import backend_or_unimplemented, clean_id, missing_id

SOURCE = "캠페인 실행 (백엔드 execution_service)"
REASON = "백엔드 /execution API(승인 기록·메일·발송·예약) 연결 전"

# 사람의 명시적 승인 없이는 호출하지 않는 Tool (메일·파일 전달·발송·예약)
GUARDED_TOOLS = frozenset({"send_approval_email", "deliver_approved_page", "send_campaign_messages",
                           "schedule_campaign_followup"})


def _execution(method: str, campaign_id: str, action: str) -> dict:
    campaign_id = clean_id(campaign_id)
    if not campaign_id and (blocked := missing_id(SOURCE, "campaign_id")):
        return blocked
    return backend_or_unimplemented(method, f"/execution/{campaign_id}/{action}", source=SOURCE, reason=REASON)


def send_approval_email(campaign_id: str) -> dict:
    """팀장에게 보고서 링크와 승인 요청 메일 발송 (마케터 승인 + 필수 검증 통과 시에만 백엔드가 발송)."""
    return _execution("POST", campaign_id, "approval-request")


def track_approval_status(campaign_id: str) -> dict:
    """승인·수정 요청·반려 상태 조회 (승인 기록 조회만, 상태를 바꾸지 않음)."""
    return _execution("GET", campaign_id, "approval-status")


def deliver_approved_page(campaign_id: str) -> dict:
    """승인 스냅샷 HTML 파일을 마케터에게 전달."""
    return _execution("POST", campaign_id, "page-delivery")


def check_campaign_url(campaign_id: str) -> dict:
    """등록된 기획전 페이지 URL 접근 확인."""
    return _execution("POST", campaign_id, "url-check")


def send_campaign_messages(campaign_id: str) -> dict:
    """확정된 타깃에게 메시지 발송 (시작일 전이면 예약 등록, 이후면 발송 — 백엔드 게이트 통과 시)."""
    return _execution("POST", campaign_id, "send")


def schedule_campaign_followup(campaign_id: str) -> dict:
    """발송 결과 및 사후 확인(2주·4주 측정, 트렌드 재확인) 예약."""
    return _execution("POST", campaign_id, "followups")


TOOLS = {
    "send_approval_email": send_approval_email,
    "track_approval_status": track_approval_status,
    "deliver_approved_page": deliver_approved_page,
    "check_campaign_url": check_campaign_url,
    "send_campaign_messages": send_campaign_messages,
    "schedule_campaign_followup": schedule_campaign_followup,
}
TOOL_SOURCE = {name: "execution" for name in TOOLS}


def data_status() -> dict:
    return {"execution": "백엔드 API로 실행 (승인된 Tool만)" if backend_client.enabled() else f"미구현 ({REASON})"}
