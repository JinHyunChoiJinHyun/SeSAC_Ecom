"""Orchestrator Tool — update_conversation_state: 새로운 결정 사항을 대화 상태에 반영.

대화 상태(OrchestratorState)의 `campaign` 칸에 기획전 확정값을 저장합니다.
(State 클래스가 아니라 함수 모음입니다. 분석 그래프 전용 State는 common_tools/analysis_state.py의 AnalysisState)
Checkpointer가 thread_id별로 함께 저장하므로 다음 턴·봇 재시작 후에도 그대로 참조됩니다.

campaign 형식
    {
      "values": {"category": "니트", "products": "...", "target": "...", "schedule": "...", "theme": "...", "note": "..."},
      "stage":  "category_confirmed" 처럼 가장 최근에 확정한 단계 (manage_workflow가 참고),
      "log":    [{"at", "key", "value", "previous", "source"}]   최근 30건
    }

값을 넣는 곳
    - 사용자 결정(예: "카테고리는 니트로 확정") → 승인(check_approval) 후 반영
    - 특화 Agent 결과의 "decisions" dict → 바로 반영, "approval_request" → 승인 후 반영
"""

from datetime import datetime, timezone

# 확정값 항목과 한글 이름 (프롬프트·안내 문구에 사용)
CAMPAIGN_KEYS = {
    "category": "카테고리",
    "products": "주력 상품",
    "target": "타깃",
    "schedule": "기획전 일정",
    "theme": "기획전 주제",
    "note": "메모",
}
MAX_LOG = 30


def empty_campaign() -> dict:
    return {"values": {}, "stage": "", "log": []}


def normalize_updates(updates) -> dict:
    """[{"key","value"}] 또는 {key: value} → 등록된 key만 남긴 {key: value}."""

    if isinstance(updates, dict):
        items = updates.items()
    else:
        items = [((u.get("key") if isinstance(u, dict) else getattr(u, "key", "")),
                  (u.get("value") if isinstance(u, dict) else getattr(u, "value", ""))) for u in (updates or [])]
    result = {}
    for key, value in items:
        key = str(key or "").strip().lower()
        value = str(value or "").strip()
        if key in CAMPAIGN_KEYS and value:
            result[key] = value
    return result


def update_conversation_state(campaign: dict | None, updates, source: str = "user") -> dict:
    """확정값을 반영한 새 campaign dict를 돌려줍니다 (원본은 바꾸지 않음)."""

    campaign = dict(campaign or empty_campaign())
    values = dict(campaign.get("values") or {})
    log = list(campaign.get("log") or [])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = normalize_updates(updates)
    for key, value in changed.items():
        log.append({"at": now, "key": key, "value": value, "previous": values.get(key, ""), "source": source})
        values[key] = value
    campaign["values"] = values
    campaign["log"] = log[-MAX_LOG:]
    if changed:
        campaign["stage"] = f"{list(changed)[-1]}_confirmed"
    return campaign


def describe_updates(updates) -> str:
    return "\n".join(f"- {CAMPAIGN_KEYS.get(k, k)}: {v}" for k, v in normalize_updates(updates).items())


def format_campaign(campaign: dict | None) -> str:
    """manage_context: LLM 입력용 확정값 텍스트."""

    values = (campaign or {}).get("values") or {}
    if not values:
        return "(확정된 값 없음)"
    return "\n".join(f"- {CAMPAIGN_KEYS.get(k, k)}: {v}" for k, v in values.items())
