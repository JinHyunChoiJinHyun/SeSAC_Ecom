"""Orchestrator Tool — manage_workflow: 대화 단계에 따라 실행 흐름 제어 (Conditional Edge).

그래프의 조건부 엣지 함수입니다 (graph.py에서 add_conditional_edges로 연결).

    orchestrator ──after_orchestrator──▶ <특화 Agent> | check_approval | END
    <특화 Agent>  ──after_agent─────────▶ check_approval | END
    check_approval ─────────────────────▶ END

판단 기준
    - 승인 대기(pending_approval)가 있으면 → check_approval (interrupt로 멈춤)
    - 업무 요청이면 → Orchestrator가 고른 Agent
    - 그 밖(일상 대화) → END

전문 Agent가 구현되면 campaign["stage"](예: category_confirmed)에 따라 다음 Agent를 고르는
규칙을 next_agent_for_stage()에 추가합니다 (예: category_confirmed → product_analysis).
"""

from langgraph.graph import END

CHECK_APPROVAL = "check_approval"

# 확정 단계 → 다음에 실행할 Agent (전문 Agent 구현 후 채움). 비어 있으면 자동 진행하지 않음
STAGE_NEXT_AGENT: dict[str, str] = {
    # "category_confirmed": "product_analysis",
    # "products_confirmed": "content_generation",
}


def next_agent_for_stage(campaign: dict | None, enabled: set[str]) -> str | None:
    stage = (campaign or {}).get("stage", "")
    agent = STAGE_NEXT_AGENT.get(stage)
    return agent if agent in enabled else None


def after_orchestrator(state) -> str:
    if state.get("turn_type") == "analysis" and state.get("route"):
        return state["route"]
    if state.get("pending_approval"):
        return CHECK_APPROVAL
    return END


def after_agent(state) -> str:
    return CHECK_APPROVAL if state.get("pending_approval") else END
