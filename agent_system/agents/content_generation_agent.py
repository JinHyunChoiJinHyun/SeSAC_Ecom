"""콘텐츠 생성 Agent — 기존 기획전 조건·승인 흐름을 유지하면서 소구점, 카피, 고객군별 문구, 페이지 시안을 생성·수정합니다."""

# 기존 워크플로에서 호출하는 로컬 Tool 목록 (LLM에는 연결하지 않음).
from agent_system.agents.local_workflow import execute_tool, freeze_tools, workflow_tool

WORKFLOW_TOOLS = freeze_tools({
    'build_summary': ('agent_system.orchestrator.tools.campaign_selection', 'build_summary'),
    'build_selling_point_evidence': ('agent_system.orchestrator.tools.campaign_selection', 'build_selling_point_evidence'),
    'generate_candidates': ('agent_system.orchestrator.tools.campaign_copy', 'generate_candidates'),
    'regenerate_copy': ('agent_system.orchestrator.tools.copy_revision', 'regenerate_copy'),
    'replay_copy_step': ('agent_system.orchestrator.tools.copy_revision', 'replay_copy_step'),
    'create_initial_preview': ('agent_system.orchestrator.tools.campaign_page', 'create_initial_preview'),
    'revise_preview': ('agent_system.orchestrator.tools.campaign_page', 'revise_preview'),
    'regenerate_preview': ('agent_system.orchestrator.tools.campaign_page', 'regenerate_preview'),
    'undo_preview': ('agent_system.orchestrator.tools.campaign_page', 'undo_preview'),
    'render_preview_image': ('agent_system.orchestrator.tools.campaign_page', 'render_preview_image'),
    'handle_segment_copy': ('agent_system.orchestrator.tools.campaign_copy_r41', 'handle'),
    'handle_final_page': ('agent_system.orchestrator.tools.campaign_page_final_r428', 'handle'),
    'handle_final_page_button': ('agent_system.orchestrator.tools.campaign_page_final_r428', 'handle_button'),
})

def run(action, /, *args, **kwargs):
    """기존 워크플로의 조건 검사 이후 선택된 Tool을 실행한다."""
    return execute_tool(WORKFLOW_TOOLS, action, *args, **kwargs)

build_summary = workflow_tool('content_generation', 'build_summary')
build_selling_point_evidence = workflow_tool('content_generation', 'build_selling_point_evidence')
generate_candidates = workflow_tool('content_generation', 'generate_candidates')
regenerate_copy = workflow_tool('content_generation', 'regenerate_copy')
replay_copy_step = workflow_tool('content_generation', 'replay_copy_step')
create_initial_preview = workflow_tool('content_generation', 'create_initial_preview')
revise_preview = workflow_tool('content_generation', 'revise_preview')
regenerate_preview = workflow_tool('content_generation', 'regenerate_preview')
undo_preview = workflow_tool('content_generation', 'undo_preview')
render_preview_image = workflow_tool('content_generation', 'render_preview_image')
handle_segment_copy = workflow_tool('content_generation', 'handle_segment_copy')
handle_final_page = workflow_tool('content_generation', 'handle_final_page')
handle_final_page_button = workflow_tool('content_generation', 'handle_final_page_button')
