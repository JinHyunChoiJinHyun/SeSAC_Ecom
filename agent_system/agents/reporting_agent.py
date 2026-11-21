"""⑥ Reporting Agent — 결과 정리 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 각 에이전트의 결과를 시각화하고 실행 문서로 통합
시나리오: 메인 3·7·9·12·18·23단계(시각화·실행 계획 보고서), 서브 4단계(한 장짜리 촬영 요청서 PDF)

문서 작성은 모두 이 Agent가 담당합니다 (11/20 사용자 결정)
    create_report(kind, ...)      "보고서 만들어줘" → generate_analysis_report (일반 분석 보고서 Word 초안)
                                  "기획전 보고서"   → generate_campaign_report (기획전 실행 계획 보고서 Word 초안)
                                  Orchestrator(tools/report.py)는 요청을 알아보고 여기로 넘기기만 합니다.
    export_uploaded_docx(docx)    마케터가 Slack에 올린 Word 수정본 → export_report_pdf → 최종 PDF
                                  (interfaces/slack/report_finalize.py가 호출)
    두 진입점은 할 일이 정해져 있어 LLM 없이 Tool을 코드로 부릅니다. Tool 호출은 run_logged_tool로 로그를 남깁니다 (팀 규칙).

구현 상태 (11/18 팀 원칙: 없는 부분은 가짜로 만들지 않고 미구현)
- Tool 이름·기능·백엔드 API 경로는 agent_system/tools/reporting_tools.py (설계안 4개 + generate_analysis_report)
- 보고서·PDF는 로컬로 동작. 그래프(visualize_analysis_results)·촬영 요청서(generate_photography_brief)는 백엔드 연결 전 '미구현'.
- run(request)(LLM이 Tool을 고르는 경로)은 백엔드 연결 전에는 LLM을 부르지 않고 '미구현' 안내 (registry enabled=False).
"""

from __future__ import annotations

from pathlib import Path

from agent_system import backend_client
from agent_system.base_agent import ToolAgentConfig, run_tool_agent
from agent_system.common_tools.tool_call_log import log_tool_summary, run_logged_tool
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
    table_priority=("visualize_analysis_results", "generate_campaign_report", "generate_photography_brief",
                    "export_report_pdf", "generate_analysis_report"),
)


def get_reporting_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    return run_tool_agent(CONFIG, request, lambda cfg: get_reporting_model(cfg))


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
        result, record = run_logged_tool(AGENT_NAME, tool, T.TOOLS[tool], args)
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
             "최종 PDF는 수정본을 기준으로 생성합니다."]
    if kind == "analysis":
        lines += [f"- {i}. {q}" for i, q in enumerate(data.get("entries") or [], start=1)]
    elif data.get("note"):
        lines.append(data["note"])
    return {"reply": "\n".join(lines), "attachments": files, "errors": {}, "tool_result": result}


def export_uploaded_docx(docx: Path, out_dir: Path) -> Path:
    """마케터가 올린 Word 수정본 → 최종 PDF (export_report_pdf Tool). 실패하면 예외."""
    report_id = T.new_upload_id()
    with T.report_context(files={report_id: Path(docx)}, out_dir=Path(out_dir)):
        result, record = run_logged_tool(AGENT_NAME, "export_report_pdf", T.export_report_pdf, {"report_id": report_id})
    log_tool_summary(AGENT_NAME, [record])
    files = ((result.get("data") or {}).get("files") or []) if result.get("available") else []
    if not files:
        raise RuntimeError(result.get("status") or "PDF 변환 실패")
    return Path(files[0])
