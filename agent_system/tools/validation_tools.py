"""⑤ Validation Agent Tool 3개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    validate_data_evidence        분석 수치, 출처, 상품 속성의 근거 확인
                                  (H&M 상품 속성 문구의 근거가 무신사 리뷰(external_review)뿐이면 실패)
    validate_marketing_copy       허위·과장 표현, 미확인 수치, 금지 문구 검사
    validate_campaign_conditions  기획전 기간, 혜택, 링크, 발송 조건 확인

아키텍처 [3]: 규칙 검사는 백엔드(validation_service, 결과는 validation_result에 content_hash 단위로 저장),
이 Agent는 맥락이 필요한 의미 판단을 보완합니다.
    POST /validation/evidence   POST /validation/copy   POST /validation/conditions
    (아키텍처에는 validate_url · validate_url_content도 있으나 설계안 기준 3개만 둡니다. URL 확인은 ⑦ check_campaign_url)
로컬 모드: 미구현. 검사하지 않은 결과를 '통과'로 만들지 않습니다.
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.tools.common import backend_or_unimplemented, clean_id

SOURCE = "검증 규칙 (백엔드 validation_service)"
REASON = "백엔드 /validation API(규칙 검사·검증 기록) 연결 전"


def validate_data_evidence(content_id: str) -> dict:
    """분석 수치·출처·상품 속성의 근거 확인. 무신사 리뷰를 H&M 상품 특성의 근거로 쓴 문구는 실패 처리."""
    return backend_or_unimplemented("POST", "/validation/evidence", source=SOURCE, reason=REASON,
                                    body={"content_id": clean_id(content_id)})


def validate_marketing_copy(content_id: str) -> dict:
    """허위·과장 표현, 근거 없는 수치, 금지 문구, 과거 기획전 수치·혜택 복사 검사."""
    return backend_or_unimplemented("POST", "/validation/copy", source=SOURCE, reason=REASON,
                                    body={"content_id": clean_id(content_id)})


def validate_campaign_conditions(campaign_id: str, content_id: str | None = None) -> dict:
    """기획전 기간, 혜택, 링크 슬롯, 발송 조건(연락 가능·발송 상한·야간 시간) 확인."""
    return backend_or_unimplemented("POST", "/validation/conditions", source=SOURCE, reason=REASON,
                                    body={"campaign_id": clean_id(campaign_id), "content_id": clean_id(content_id)})


TOOLS = {
    "validate_data_evidence": validate_data_evidence,
    "validate_marketing_copy": validate_marketing_copy,
    "validate_campaign_conditions": validate_campaign_conditions,
}
TOOL_SOURCE = {name: "validation" for name in TOOLS}


def data_status() -> dict:
    return {"validation": "백엔드 API로 검사" if backend_client.enabled() else f"미구현 ({REASON})"}
