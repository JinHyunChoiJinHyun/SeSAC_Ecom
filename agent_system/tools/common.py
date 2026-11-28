"""로컬 Tool 공통 결과 및 식별자 처리."""

from __future__ import annotations


NOT_READY_NOTE = "미구현 단계입니다. 결과·수치·문구를 추정하거나 대신 만들지 마십시오."


def unimplemented(source: str, reason: str, **data) -> dict:
    return {"available": False, "not_ready": True, "status": f"미구현 ({reason})", "source": source,
            "data": data, "evidence": [], "note": NOT_READY_NOTE}




def clean_id(value) -> str:
    return str(value or "").strip()
