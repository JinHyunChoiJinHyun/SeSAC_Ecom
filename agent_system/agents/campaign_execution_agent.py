"""캠페인 실행 Agent — 기존 수신자 확인, 승인, 첨부파일 검사, 최종 발송 흐름을 담당합니다. 실제 고객 대량 발송은 하지 않습니다."""

# 기존 워크플로에서 호출하는 로컬 Tool 목록 (LLM에는 연결하지 않음).
from agent_system.agents.local_workflow import execute_tool, freeze_tools, workflow_tool

WORKFLOW_TOOLS = freeze_tools({
    'route_recipient_flow': ('agent_system.approval_demo.recipient_flow', 'route_recipient_flow'),
    'register_campaign_report': ('agent_system.approval_demo.recipient_flow', 'register_campaign_report'),
    'mark_pdf_ready': ('agent_system.approval_demo.recipient_flow', 'mark_pdf_ready'),
    'register_png': ('agent_system.approval_demo.delivery_r42', 'register_png'),
    'start_recipient_confirmation': ('agent_system.approval_demo.recipient_flow', 'start_recipient_confirmation'),
    'prepare_send': ('agent_system.approval_demo.delivery_r42', 'prepare_send'),
    'post_buttons': ('agent_system.approval_demo.delivery_r42', 'post_buttons'),
    'revise_mail': ('agent_system.approval_demo.delivery_r42', 'revise_mail'),
    'confirm_recipient': ('agent_system.approval_demo.recipient_flow', 'confirm_recipient'),
    'sync_delivery': ('agent_system.orchestrator.tools.campaign_page_final_r428', 'sync_delivery'),
    'run_approval_demo_command': ('agent_system.approval_demo.slack_flow', 'run_approval_demo_command'),
})

def run(action, /, *args, **kwargs):
    """기존 워크플로의 조건 검사 이후 선택된 Tool을 실행한다."""
    return execute_tool(WORKFLOW_TOOLS, action, *args, **kwargs)

route_recipient_flow = workflow_tool('campaign_execution', 'route_recipient_flow')
register_campaign_report = workflow_tool('campaign_execution', 'register_campaign_report')
mark_pdf_ready = workflow_tool('campaign_execution', 'mark_pdf_ready')
register_png = workflow_tool('campaign_execution', 'register_png')
start_recipient_confirmation = workflow_tool('campaign_execution', 'start_recipient_confirmation')
prepare_send = workflow_tool('campaign_execution', 'prepare_send')
post_buttons = workflow_tool('campaign_execution', 'post_buttons')
revise_mail = workflow_tool('campaign_execution', 'revise_mail')
confirm_recipient = workflow_tool('campaign_execution', 'confirm_recipient')
sync_delivery = workflow_tool('campaign_execution', 'sync_delivery')
run_approval_demo_command = workflow_tool('campaign_execution', 'run_approval_demo_command')
