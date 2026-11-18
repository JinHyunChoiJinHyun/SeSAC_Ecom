"""⑥ Reporting Agent — 결과 정리 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 각 에이전트의 결과를 시각화하고 실행 문서로 통합
시나리오: 메인 3·7·9·12·18·23단계(시각화·실행 계획 보고서), 서브 4단계(한 장짜리 촬영 요청서 PDF)

구현 상태 (11/18 팀 원칙: 없는 부분은 가짜로 만들지 않고 미구현)
- Tool 이름·기능·백엔드 API 경로는 확정 (agent_system/tools/reporting_tools.py)
- 백엔드 API가 연결되기 전(CART_SIGNAL_BACKEND_URL 없음)에는 LLM을 부르지 않고 '미구현' 안내를 돌려줍니다 (not_ready=True).
- registry.py에서는 enabled=False. 데이터·모델·백엔드가 연결되면 True로 바꾸면 Orchestrator·워크플로가 바로 사용합니다.
- 실행 흐름은 agent_system/base_agent.py (Tool 호출 로그 포함, 팀 규칙)
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.base_agent import ToolAgentConfig, run_tool_agent
from agent_system.prompts.reporting_prompt import REPORTING_PROMPT
from agent_system.tools import reporting_tools as T

AGENT_NAME = "reporting"

CONFIG = ToolAgentConfig(
    name=AGENT_NAME,
    label="결과 정리",
    tools=T.TOOLS,
    tool_source=T.TOOL_SOURCE,
    prompt=REPORTING_PROMPT,
    data_status=T.data_status,
    is_ready=backend_client.enabled,
    scope_note="분석 근거 스냅샷·확정값 기준",
    table_priority=("visualize_analysis_results", "generate_campaign_report", "generate_photography_brief", "export_report_pdf"),
)


def get_reporting_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    return run_tool_agent(CONFIG, request, lambda cfg: get_reporting_model(cfg))
