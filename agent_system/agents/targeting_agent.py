"""③ Targeting Agent — 고객 타깃 선정 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 고객 구매 이력과 취향을 기반으로 고객별 추천 상품 및 발송 대상 결정
시나리오: 메인 3단계(17·19단계): 타깃 점수·고객별 추천 상품 → 세분화 → 발송 명단(대조군 10%)

구현 상태 (11/18 팀 원칙: 없는 부분은 가짜로 만들지 않고 미구현)
- Tool 이름·기능·백엔드 API 경로는 확정 (agent_system/tools/targeting_tools.py)
- 백엔드 API가 연결되기 전(CART_SIGNAL_BACKEND_URL 없음)에는 LLM을 부르지 않고 '미구현' 안내를 돌려줍니다 (not_ready=True).
- registry.py에서는 enabled=False. 데이터·모델·백엔드가 연결되면 True로 바꾸면 Orchestrator·워크플로가 바로 사용합니다.
- 실행 흐름은 agent_system/base_agent.py (Tool 호출 로그 포함, 팀 규칙)
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.base_agent import ToolAgentConfig, run_tool_agent
from agent_system.prompts.targeting_prompt import TARGETING_PROMPT
from agent_system.tools import targeting_tools as T

AGENT_NAME = "targeting"

CONFIG = ToolAgentConfig(
    name=AGENT_NAME,
    label="타깃 선정",
    tools=T.TOOLS,
    tool_source=T.TOOL_SOURCE,
    prompt=TARGETING_PROMPT,
    data_status=T.data_status,
    is_ready=backend_client.enabled,
    scope_note="H&M 고객 구매 이력 기준 (예측 모델·고객 피처는 팀원 담당)",
    table_priority=("build_campaign_audience", "segment_customers", "recommend_customer_products", "calculate_target_scores"),
)


def get_targeting_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    return run_tool_agent(CONFIG, request, lambda cfg: get_targeting_model(cfg))
