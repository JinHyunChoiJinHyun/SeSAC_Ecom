"""⑤ Validation Agent — 검증 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 생성된 결과물이 실제 데이터와 승인된 조건에 부합하는지 확인
시나리오: 메인 15·21단계(페이지 틀·메시지 근거 검사), 보고서 수치 검사 / 서브: 촬영 요청서 근거 확인

구현 상태 (11/18 팀 원칙: 없는 부분은 가짜로 만들지 않고 미구현)
- Tool 이름·기능·백엔드 API 경로는 확정 (agent_system/tools/validation_tools.py)
- 백엔드 API가 연결되기 전(CART_SIGNAL_BACKEND_URL 없음)에는 LLM을 부르지 않고 '미구현' 안내를 돌려줍니다 (not_ready=True).
- registry.py에서는 enabled=False. 데이터·모델·백엔드가 연결되면 True로 바꾸면 Orchestrator·워크플로가 바로 사용합니다.
- 실행 흐름은 agent_system/base_agent.py (Tool 호출 로그 포함, 팀 규칙)
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.base_agent import ToolAgentConfig, run_tool_agent
from agent_system.prompts.validation_prompt import VALIDATION_PROMPT
from agent_system.tools import validation_tools as T

AGENT_NAME = "validation"

CONFIG = ToolAgentConfig(
    name=AGENT_NAME,
    label="검증",
    tools=T.TOOLS,
    tool_source=T.TOOL_SOURCE,
    prompt=VALIDATION_PROMPT,
    data_status=T.data_status,
    is_ready=backend_client.enabled,
    scope_note="백엔드 규칙 검사 결과 기준 (무신사 리뷰를 H&M 특성으로 단정한 표현은 실패)",
    table_priority=("validate_data_evidence", "validate_marketing_copy", "validate_campaign_conditions"),
)


def get_validation_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    return run_tool_agent(CONFIG, request, lambda cfg: get_validation_model(cfg))
