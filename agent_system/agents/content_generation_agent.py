"""④ Content Generation Agent — 콘텐츠 생성 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 확정된 상품·소구점·타깃 정보를 바탕으로 마케팅 콘텐츠 생성
시나리오: 메인 14·20단계(기획전 문구·페이지 틀·세그먼트 메시지), 서브 3단계(필수 컷·추가 컷). 촬영 요청서는 결과 정리 담당

구현 상태 (11/18 팀 원칙: 없는 부분은 가짜로 만들지 않고 미구현)
- Tool 이름·기능·백엔드 API 경로는 확정 (agent_system/tools/content_tools.py)
- 백엔드 API가 연결되기 전(CART_SIGNAL_BACKEND_URL 없음)에는 LLM을 부르지 않고 '미구현' 안내를 돌려줍니다 (not_ready=True).
- registry.py에서는 enabled=False. 데이터·모델·백엔드가 연결되면 True로 바꾸면 Orchestrator·워크플로가 바로 사용합니다.
- 실행 흐름은 agent_system/base_agent.py (Tool 호출 로그 포함, 팀 규칙)
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.base_agent import ToolAgentConfig, run_tool_agent
from agent_system.prompts.content_generation_prompt import CONTENT_GENERATION_PROMPT
from agent_system.tools import content_tools as T

AGENT_NAME = "content_generation"

CONFIG = ToolAgentConfig(
    name=AGENT_NAME,
    label="콘텐츠 생성",
    tools=T.TOOLS,
    tool_source=T.TOOL_SOURCE,
    prompt=CONTENT_GENERATION_PROMPT,
    data_status=T.data_status,
    is_ready=backend_client.enabled,
    scope_note="확정값·분석 근거만 사용 (백엔드 슬롯 규칙·템플릿)",
    table_priority=("generate_campaign_copy", "generate_segment_messages", "plan_product_photography", "build_campaign_page"),
)


def get_content_generation_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    return run_tool_agent(CONFIG, request, lambda cfg: get_content_generation_model(cfg))
