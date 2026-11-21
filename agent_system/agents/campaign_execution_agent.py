"""⑦ Campaign Execution Agent — 캠페인 실행 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 승인 절차 및 실제 마케팅 실행 관리 (명시적 승인 없이 자동 실행하지 않음)
시나리오: 메인 25~29단계: 팀장 승인 요청 → 승인 상태 확인 → 페이지 전달 → URL 확인 → 발송·사후 확인 예약

구현 상태 (11/18 팀 원칙: 없는 부분은 가짜로 만들지 않고 미구현)
- Tool 이름·기능·백엔드 API 경로는 확정 (agent_system/tools/execution_tools.py)
- 백엔드 API가 연결되기 전(CART_SIGNAL_BACKEND_URL 없음)에는 LLM을 부르지 않고 '미구현' 안내를 돌려줍니다 (not_ready=True).
- registry.py에서는 enabled=False. 데이터·모델·백엔드가 연결되면 True로 바꾸면 Orchestrator·워크플로가 바로 사용합니다.
- 실행 흐름은 agent_system/base_agent.py (Tool 호출 로그 포함, 팀 규칙)
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.base_agent import ToolAgentConfig, run_tool_agent
from agent_system.prompts.campaign_execution_prompt import CAMPAIGN_EXECUTION_PROMPT
from agent_system.tools import execution_tools as T

AGENT_NAME = "campaign_execution"

CONFIG = ToolAgentConfig(
    name=AGENT_NAME,
    label="캠페인 실행",
    tools=T.TOOLS,
    tool_source=T.TOOL_SOURCE,
    prompt=CAMPAIGN_EXECUTION_PROMPT,
    data_status=T.data_status,
    is_ready=backend_client.enabled,
    scope_note="승인 기록·발송 게이트는 백엔드가 최종 판단",
    table_priority=("track_approval_status", "check_campaign_url"),
    guarded_tools=T.GUARDED_TOOLS,
)


def get_campaign_execution_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    return run_tool_agent(CONFIG, request, lambda cfg: get_campaign_execution_model(cfg))
