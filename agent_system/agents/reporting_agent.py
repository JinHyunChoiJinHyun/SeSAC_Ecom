"""로컬 보고서 작성 워크플로. 기존 호출 경로와 Tool 로그를 유지한다."""

from __future__ import annotations

from pathlib import Path

from agent_system.common_tools.tool_call_log import log_tool_summary, run_logged_tool
from agent_system.tools import reporting_tools as T

AGENT_NAME = "reporting"







# ---------------------------------------------------------------- 문서 작성 진입점 (LLM 없음, Tool은 로그와 함께 호출)
def create_report(kind: str, analysis_log: list | None, campaign: dict | None, run: dict | None,
                  report_dir, now=None) -> dict:
    """보고서 요청 처리. kind = "analysis"(일반 분석 보고서) / "campaign"(기획전 실행 계획 보고서).

    반환: {"reply": Slack 답변, "attachments": [Word 초안 경로], "errors": {...}, "tool_result": ToolResult}
    """
    tool = "generate_campaign_report" if kind == "campaign" else "generate_analysis_report"
    args = {"campaign_id": str((campaign or {}).get("campaign_id") or "")} if kind == "campaign" else {}
    with T.report_context(analysis_log=list(analysis_log or []), campaign=campaign or {}, run=run or {},
                          report_dir=Path(report_dir), now=now):
        result, record = run_logged_tool(AGENT_NAME, tool, globals()[tool], args)
    log_tool_summary(AGENT_NAME, [record])

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    files = [f for f in data.get("files") or [] if f]
    if not result.get("available") or not files:
        status = result.get("status") or ""
        if status.startswith("Tool 오류"):   # Word 쓰기 실패 등 (run_logged_tool이 예외를 결과로 바꿈)
            return {"reply": f"보고서를 만들지 못했습니다.\n- DOCX 생성 실패: {status}", "attachments": [],
                    "errors": {"docx": status}, "tool_result": result}
        # 재료 없음(분석 기록·확정값 없음) 등: 이유를 그대로 안내
        return {"reply": status or "보고서를 만들지 못했습니다.", "attachments": [], "errors": {}, "tool_result": result}
    lines = [f"{data.get('label', '보고서')} Word 초안을 만들었습니다.",
             "내용을 검토하고 Word에서 수정한 뒤 Slack에 다시 첨부해 주세요.",
             "수정한 Word 파일을 다시 첨부하면 PDF로 자동 변환됩니다."]
    if kind == "analysis":
        lines += [f"- {i}. {q}" for i, q in enumerate(data.get("entries") or [], start=1)]
    elif data.get("note") and not str(data["note"]).startswith("백엔드 보고서 기능 연결 전"):
        lines.append(data["note"])
    return {"reply": "\n".join(lines), "attachments": files, "errors": {}, "tool_result": result}


def export_uploaded_docx(docx: Path, out_dir: Path) -> Path:
    """마케터가 올린 Word 수정본 → 최종 PDF (export_report_pdf Tool). 실패하면 예외."""
    report_id = T.new_upload_id()
    with T.report_context(files={report_id: Path(docx)}, out_dir=Path(out_dir)):
        result, record = run_logged_tool(AGENT_NAME, "export_report_pdf", export_report_pdf, {"report_id": report_id})
    log_tool_summary(AGENT_NAME, [record])
    files = ((result.get("data") or {}).get("files") or []) if result.get("available") else []
    if not files:
        raise RuntimeError(result.get("status") or "PDF 변환 실패")
    return Path(files[0])

# 기존 워크플로에서 호출하는 로컬 Tool 목록 (LLM에는 연결하지 않음).
from agent_system.agents.local_workflow import execute_tool, freeze_tools, workflow_tool

WORKFLOW_TOOLS = freeze_tools({
    'build_category_summary': ('agent_system.orchestrator.tools.category_synthesis', 'build_category_summary'),
    'product_summary': ('agent_system.orchestrator.tools.category_synthesis', 'product_summary'),
    'build_draft': ('agent_system.orchestrator.tools.provisional_report', 'build_draft'),
    'revise_draft': ('agent_system.orchestrator.tools.provisional_report', 'revise_draft'),
    'generate_campaign_report': ('agent_system.tools.reporting_tools', 'generate_campaign_report'),
    'generate_analysis_report': ('agent_system.tools.reporting_tools', 'generate_analysis_report'),
    'export_report_pdf': ('agent_system.tools.reporting_tools', 'export_report_pdf'),
})

def run(action, /, *args, **kwargs):
    """기존 워크플로의 조건 검사 이후 선택된 Tool을 실행한다."""
    return execute_tool(WORKFLOW_TOOLS, action, *args, **kwargs)

build_category_summary = workflow_tool('reporting', 'build_category_summary')
product_summary = workflow_tool('reporting', 'product_summary')
build_draft = workflow_tool('reporting', 'build_draft')
revise_draft = workflow_tool('reporting', 'revise_draft')
generate_campaign_report = workflow_tool('reporting', 'generate_campaign_report')
generate_analysis_report = workflow_tool('reporting', 'generate_analysis_report')
export_report_pdf = workflow_tool('reporting', 'export_report_pdf')
