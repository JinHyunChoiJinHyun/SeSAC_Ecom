"""Orchestrator Tool ④ — 라우터: 업무 요청을 처리할 특화 Agent 선택.

대화 판단(tools/intent.py)이 "남성은?" 같은 후속 질문을 완전한 질문으로 바꾼 뒤 넘기므로
라우터는 그 질문만 보고 등록된 Agent(registry.py) 중 하나를 고르면 됩니다.

판단 순서
    1. 사용 가능한 Agent가 1개뿐이면 → 그 Agent (LLM 호출 없음)
    2. 후속 질문이고 직전 분석을 처리한 Agent가 있으면 → 같은 Agent
       (settings.KEEP_ROUTE_ON_FOLLOWUP = True일 때)
    3. 그 밖에는 LLM이 등록된 Agent 설명을 보고 선택
    4. LLM 실패·등록되지 않은 이름 → 기본 Agent (is_default=True)
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agent_system.orchestrator.registry import AgentSpec, get_routable_agents, get_default_agent


class RouteDecision(BaseModel):
    """Orchestrator 판단 결과."""

    route: str = Field(description="처리할 Agent 이름 (등록된 name 중 하나)")
    reason: str = Field(default="", description="선택 이유 한 문장")


ORCHESTRATOR_PROMPT_TEMPLATE = """
당신은 CART SIGNAL의 Orchestrator입니다.
대화 판단 단계가 넘긴 업무 요청을 처리할 특화 Agent 하나를 고릅니다.
당신은 분석을 직접 하지 않고, Agent 이름(route)과 이유만 반환합니다.

========================================
[등록된 Agent]
========================================
@@AGENT_LIST@@

========================================
[선택 규칙]
========================================
1. 질문이 요구하는 결과물을 가장 직접 만들어 내는 Agent를 고르십시오.
2. 특화 Agent의 설명에 정확히 맞지 않는 일반 조회·집계·그래프 요청은 기본 Agent(@@DEFAULT@@)로 보내십시오.
3. route에는 위 목록의 name만 쓰십시오.
""".strip()


def build_routing_prompt(agents: list[AgentSpec] | None = None) -> str:
    agents = get_routable_agents() if agents is None else agents
    lines = []
    for spec in agents:
        lines.append(f"- name: {spec.name}  ({spec.label}){'  [기본]' if spec.is_default else ''}")
        lines.append(f"  설명: {spec.description}")
        for example in spec.examples:
            lines.append(f"  예시: {example}")
    return (
        ORCHESTRATOR_PROMPT_TEMPLATE
        .replace("@@AGENT_LIST@@", "\n".join(lines))
        .replace("@@DEFAULT@@", get_default_agent().name)
    )


def get_routing_agent(model_config=None):
    """현재 요청에 고정된 모델로 라우팅 LLM을 만듭니다."""

    from env.gpt_client import get_openai_chat_client

    return create_agent(
        model=get_openai_chat_client(model_config=model_config),
        tools=[],
        response_format=ToolStrategy(RouteDecision),
        system_prompt=build_routing_prompt(),
    )


def route_request(
    question: str,
    *,
    is_followup: bool = False,
    last_route: str = "",
    llm_config: dict | None = None,
) -> RouteDecision:
    """분석 요청을 처리할 Agent를 고릅니다. 항상 사용 가능한 Agent 이름을 반환합니다."""

    from agent_system.orchestrator import settings

    agents = get_routable_agents()
    names = {spec.name for spec in agents}
    default = get_default_agent()

    # 1. Agent가 하나뿐이면 LLM 호출 없이 바로 선택
    if len(agents) == 1:
        return RouteDecision(route=agents[0].name, reason="사용 가능한 Agent가 1개입니다.")

    # 2. 후속 질문은 직전 분석을 처리한 Agent로
    if (
        getattr(settings, "KEEP_ROUTE_ON_FOLLOWUP", True)
        and is_followup
        and last_route in names
    ):
        return RouteDecision(route=last_route, reason="후속 질문이라 직전 분석과 같은 Agent를 사용합니다.")

    # 3. LLM 판단
    try:
        result = get_routing_agent(llm_config).invoke(
            {"messages": [HumanMessage(content=f"[분석 요청]\n{question}")]},
            config={"recursion_limit": 10},
        )
        decision = result.get("structured_response")
        if decision is None:
            raise ValueError("Orchestrator가 판단 결과를 반환하지 않았습니다.")
        route = str(decision.route or "").strip()
        if route not in names:
            # 4. 등록되지 않은 이름 → 기본 Agent
            return RouteDecision(
                route=default.name,
                reason=f"등록되지 않은 Agent '{route}'를 골라 기본 Agent를 사용합니다.",
            )
        return RouteDecision(route=route, reason=decision.reason)

    except Exception as exc:
        # 4. 실패해도 기본 Agent로 분석은 계속합니다.
        return RouteDecision(
            route=default.name,
            reason=f"Orchestrator 판단 실패({type(exc).__name__}) → 기본 Agent 사용",
        )
