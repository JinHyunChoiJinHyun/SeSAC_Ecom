"""검증 Agent — 분석 결과 및 데이터 근거, 카피, 상품 선택과 보고서 작성 조건을 검증합니다."""

# 기존 워크플로에서 호출하는 로컬 Tool 목록 (LLM에는 연결하지 않음).
from agent_system.agents.local_workflow import execute_tool, freeze_tools, workflow_tool

WORKFLOW_TOOLS = freeze_tools({
    'check_selection': ('agent_system.validation.specialized_selection_gate', 'check_selection'),
    'validate_with_retries': ('agent_system.validation.specialized_selection_gate', 'validate_with_retries'),
    'validate_candidates': ('agent_system.orchestrator.tools.campaign_copy', 'validate_candidates'),
    'review_campaign_copy': ('agent_system.validation.semantic_campaign_validator', 'review_campaign_copy'),
    'validated_updates': ('agent_system.orchestrator.tools.category_approval', 'validated_updates'),
    'verify_requested_updates': ('agent_system.orchestrator.tools.category_approval', 'verify_requested_updates'),
    'validate_copy_evidence': ('agent_system.validation.campaign_evidence_gate', 'validate_copy_evidence'),
    'require_approved_copy_for_report': ('agent_system.validation.campaign_evidence_gate', 'require_approved_copy_for_report'),
})

def run(action, /, *args, **kwargs):
    """기존 워크플로의 조건 검사 이후 선택된 Tool을 실행한다."""
    return execute_tool(WORKFLOW_TOOLS, action, *args, **kwargs)

check_selection = workflow_tool('validation', 'check_selection')
validate_with_retries = workflow_tool('validation', 'validate_with_retries')
validate_candidates = workflow_tool('validation', 'validate_candidates')
review_campaign_copy = workflow_tool('validation', 'review_campaign_copy')
validated_updates = workflow_tool('validation', 'validated_updates')
verify_requested_updates = workflow_tool('validation', 'verify_requested_updates')
validate_copy_evidence = workflow_tool('validation', 'validate_copy_evidence')
require_approved_copy_for_report = workflow_tool('validation', 'require_approved_copy_for_report')
