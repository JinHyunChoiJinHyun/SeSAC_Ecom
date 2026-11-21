"""전문 Agent Tool 공통 — 백엔드 API 호출 또는 '미구현' 결과.

팀 원칙(11/18): 데이터·예측 모델·백엔드 API가 없는 부분은 가짜 결과를 만들지 않고 '미구현'으로 돌려줍니다.
    - 백엔드 모드(CART_SIGNAL_BACKEND_URL 있음): 아키텍처 [3]의 API를 호출 → ToolResult
    - 그 밖: available=False, not_ready=True, status="미구현 (…)" → Agent는 결과를 추정하지 않고 미구현이라고 안내
"""

from __future__ import annotations

from agent_system import backend_client

NOT_READY_NOTE = "미구현 단계입니다. 결과·수치·문구를 추정하거나 대신 만들지 마십시오."


def unimplemented(source: str, reason: str, **data) -> dict:
    return {"available": False, "not_ready": True, "status": f"미구현 ({reason})", "source": source,
            "data": data, "evidence": [], "note": NOT_READY_NOTE}


def backend_or_unimplemented(method: str, path: str, *, source: str, reason: str,
                             params: dict | None = None, body: dict | None = None) -> dict:
    """백엔드가 연결돼 있으면 API 호출, 아니면 미구현 결과 (요청 값은 data.request로 돌려줌)."""
    if backend_client.enabled():
        return backend_client.call(method, path, params=params, body=body, source=source)
    return unimplemented(source, reason, request={"method": method, "path": path, **(params or {}), **(body or {})})


def clean_id(value) -> str:
    return str(value or "").strip()


def missing_id(source: str, name: str) -> dict | None:
    """백엔드 모드에서 필요한 ID가 없으면 사용 불가 결과 (로컬 모드는 어차피 미구현)."""
    if backend_client.enabled():
        return {"available": False, "status": f"{name}가 필요합니다 (백엔드 캠페인·콘텐츠 생성 후 발급)",
                "source": source, "data": {}, "evidence": []}
    return None
