"""⑥ Reporting Agent Tool 5개 — 문서 작성은 모두 이 Agent가 담당합니다.

    visualize_analysis_results   트렌드·상품 비교·타깃 결과 그래프 생성                         (설계안)
    generate_campaign_report     기획전 실행 계획 보고서 생성 (메인)                             (설계안)
    generate_photography_brief   촬영 우선순위·구도·상품 정보가 담긴 한 장짜리 촬영 요청서 (서브)   (설계안)
    export_report_pdf            보고서 및 촬영 요청서 PDF 출력                                   (설계안)
    generate_analysis_report     일반 분석 보고서 생성: 대화 중 분석 결과 묶음                     (11/20 추가)

백엔드 API (아키텍처 [3], scope report:write)
    POST /reports/charts   POST /reports/campaign   POST /reports/photography-brief   POST /reports/{report_id}/pdf
    POST /reports/analysis (11/20 추가 제안 — 백엔드 팀원과 확정 필요)

로컬 모드 (백엔드 보고서 API 연결 전)
    보고서 재료(대화 중 분석 기록·확정값·워크플로 진행)는 Orchestrator 대화 상태에 있으므로,
    Reporting Agent가 report_context(...) 안에서 Tool을 부를 때만 로컬로 문서를 만듭니다.
        generate_analysis_report  → 일반 분석 보고서 Word 초안 (report_export/builders.py · writers.py)
        generate_campaign_report  → 기획전 실행 계획 보고서 Word 초안 (백엔드가 있으면 백엔드 먼저, 실패하면 로컬 초안)
        export_report_pdf         → Word(마케터 수정본 또는 초안) → PDF (report_export/pdf_convert.py, LibreOffice)
    report_context 밖(LLM이 직접 고른 경우 등)에서는 지금처럼 '미구현'을 돌려줍니다 (가짜 문서 없음).
    visualize_analysis_results · generate_photography_brief는 아직 미구현(백엔드 분석 스냅샷·템플릿 필요).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_system import backend_client
from agent_system.tools.common import backend_or_unimplemented, clean_id, missing_id, unimplemented

SOURCE = "보고서·시각화 (Reporting Agent)"
REASON = "백엔드 /reports API(분석 스냅샷·보고서 템플릿) 연결 전"
LOCAL_ONLY_REASON = "로컬 보고서는 Orchestrator 보고서 요청('보고서 만들어줘')에서 Reporting Agent가 실행"
KST = timezone(timedelta(hours=9))

_CONTEXT: ContextVar[dict | None] = ContextVar("cart_signal_report_context", default=None)


@contextmanager
def report_context(**values):
    """Reporting Agent가 Tool을 부르는 동안만 보고서 재료를 넘겨 줍니다 (요청마다 따로, 스레드 안전).

    values: analysis_log, campaign, run, report_dir, now, files({report_id: docx 경로}), out_dir
    """
    token = _CONTEXT.set(dict(values))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _local() -> dict | None:
    return _CONTEXT.get()


def _stamp(ctx: dict) -> str:
    return (ctx.get("now") or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y%m%d_%H%M%S")


def _ok(data: dict, evidence: list | None = None, status: str = "로컬 생성 (Word 초안)") -> dict:
    return {"available": True, "status": status, "source": SOURCE, "data": data, "evidence": evidence or []}


def _fail(status: str, **data) -> dict:
    return {"available": False, "status": status, "source": SOURCE, "data": data, "evidence": []}


def _write_draft(doc, ctx: dict, name: str) -> tuple[str, Path]:
    from agent_system.common_tools.report_export import writers
    report_id = f"CART_SIGNAL_{name}_{_stamp(ctx)}_초안"
    path = Path(ctx["report_dir"]) / f"{report_id}.docx"
    writers.write_docx(doc, path)
    return report_id, path


# ---------------------------------------------------------------- 설계안 Tool
def visualize_analysis_results(run_id: str | None = None, snapshot_ids: list[str] | None = None,
                               chart_type: str | None = None) -> dict:
    """트렌드·상품 비교·타깃 결과 그래프 생성 (워크플로 run의 분석 근거 스냅샷 기준)."""
    return backend_or_unimplemented("POST", "/reports/charts", source=SOURCE, reason=REASON, body={
        "run_id": clean_id(run_id), "snapshot_ids": list(snapshot_ids or []), "chart_type": chart_type})


def generate_campaign_report(campaign_id: str = "") -> dict:
    """기획전 실행 계획 보고서 생성 (상품 선정·페이지·타깃 결과 종합). 로컬에서는 Word 초안."""
    ctx = _local()
    # R39의 확정 고객 집계는 로컬 보고서 정본에서 사용한다.
    r39 = ((ctx or {}).get('run') or {}).get('campaign_target_r39') or {}
    if r39.get('status') != 'confirmed' and backend_client.enabled() and (ctx is None or clean_id(campaign_id)):
        result = backend_client.call("POST", "/reports/campaign", source=SOURCE,
                                     body={"campaign_id": clean_id(campaign_id)})
        if result.get("available") or ctx is None:
            return result
        print(f"[Reporting] 백엔드 보고서 실패 → 로컬 초안 ({result.get('status')})")
    if ctx is None:
        return unimplemented(SOURCE, LOCAL_ONLY_REASON, request={"campaign_id": clean_id(campaign_id)})

    from agent_system.common_tools.report_export import builders
    campaign, run = ctx.get("campaign") or {}, ctx.get("run") or {}
    if not ((campaign.get("values") or {}) or run.get("workflow")):
        return _fail("기획전 확정값이나 진행한 워크플로가 없어 기획전 실행 계획 보고서를 만들 수 없습니다.\n"
                     "'기획전 준비 시작하자'로 워크플로를 진행하거나 확정값을 정한 뒤 다시 요청해 주세요.")
    entries = list(ctx.get("analysis_log") or [])
    doc = builders.build_campaign_report(campaign, run, entries, ctx.get("now"))
    report_id, path = _write_draft(doc, ctx, "기획전_실행계획_보고서")
    return _ok({"report_id": report_id, "kind": "campaign", "label": "기획전 실행 계획 보고서 (초안)",
                "files": [str(path)], "format": "docx", "draft": True,
                "note": "고객군별 선정 근거 및 메시지 제안을 포함했습니다." if r39.get("status") == "confirmed" else "백엔드 보고서 기능 연결 전이라 대화에 기록된 확정값·단계 결과로 만든 초안입니다 (미진행 단계는 그대로 표시)."},
               [{"source_type": "workflow_run", "source_id": run.get("run_id", ""), "label": run.get("workflow", "")}])


def generate_photography_brief(campaign_id: str, run_id: str | None = None) -> dict:
    """대표 상품 선정 근거, 컷별 촬영 포인트·구도, 추가 확인 사항을 한 장으로 정리한 촬영 요청서 생성."""
    return backend_or_unimplemented("POST", "/reports/photography-brief", source=SOURCE, reason=REASON,
                                    body={"campaign_id": clean_id(campaign_id), "run_id": clean_id(run_id)})


def export_report_pdf(report_id: str) -> dict:
    """보고서·촬영 요청서 PDF 출력. 로컬에서는 Word(마케터 수정본 또는 초안)를 PDF로 변환."""
    report_id = clean_id(report_id)
    ctx = _local()
    if ctx is not None:
        if not report_id:
            return _fail("report_id가 필요합니다")
        files = ctx.get("files") or {}
        source = Path(files[report_id]) if report_id in files else Path(ctx.get("report_dir") or ".") / f"{report_id}.docx"
        if not source.is_file():
            return _fail(f"PDF로 바꿀 Word 파일을 찾지 못했습니다 ({report_id})")
        from agent_system.common_tools.report_export import pdf_convert
        pdf = pdf_convert.convert_docx_to_pdf(source, Path(ctx.get("out_dir") or source.parent))
        return _ok({"report_id": report_id, "files": [str(pdf)], "format": "pdf",
                    "fonts": pdf_convert.pdf_font_names(pdf)}, status="로컬 변환 (LibreOffice)")
    if not report_id and (blocked := missing_id(SOURCE, "report_id")):
        return blocked
    return backend_or_unimplemented("POST", f"/reports/{report_id}/pdf", source=SOURCE, reason=REASON)


# ---------------------------------------------------------------- 11/20 추가
def generate_analysis_report(entry_ids: list[str] | None = None) -> dict:
    """일반 분석 보고서 생성: 대화 중 수행한 분석 결과(답변·표·그래프·상품 이미지·SQL)를 묶은 Word 초안.

    entry_ids: 넣을 분석 기록 ID (없으면 최근 분석 전부, 최대 10건). 기록에 있는 값만 옮기고 새 숫자를 만들지 않습니다.
    """
    ids = [clean_id(i) for i in entry_ids or [] if clean_id(i)]
    ctx = _local()
    if ctx is None:
        if backend_client.enabled():
            return backend_client.call("POST", "/reports/analysis", source=SOURCE, body={"entry_ids": ids})
        return unimplemented(SOURCE, LOCAL_ONLY_REASON, request={"entry_ids": ids})

    from agent_system.common_tools.report_export import builders
    entries = list(ctx.get("analysis_log") or [])
    if ids:
        entries = [e for e in entries if e.get("id") in ids]
    if not entries:
        return _fail("보고서로 묶을 분석 결과가 아직 없습니다. 분석을 먼저 요청해 주세요. "
                     "(예: \"H&M 여성 상품 유형별 구매 Top 10\")")
    doc = builders.build_analysis_report(entries, ctx.get("now"))
    report_id, path = _write_draft(doc, ctx, "분석_보고서")
    questions = [e.get("user_question") or e.get("question") for e in entries]
    return _ok({"report_id": report_id, "kind": "analysis", "label": f"분석 보고서 (분석 {len(entries)}건)",
                "files": [str(path)], "format": "docx", "draft": True, "entries": questions},
               [{"source_type": "analysis_log", "source_id": e.get("id", ""), "label": q}
                for e, q in zip(entries, questions)])


def new_upload_id() -> str:
    return f"upload_{uuid.uuid4().hex[:12]}"


TOOLS = {
    "visualize_analysis_results": visualize_analysis_results,
    "generate_campaign_report": generate_campaign_report,
    "generate_photography_brief": generate_photography_brief,
    "export_report_pdf": export_report_pdf,
    "generate_analysis_report": generate_analysis_report,
}
TOOL_SOURCE = {name: "report" for name in TOOLS}


def data_status() -> dict:
    if backend_client.enabled():
        return {"report": "백엔드 API로 생성 (일반 분석 보고서·Word→PDF는 로컬)"}
    return {"report": "로컬: 일반 분석 보고서·기획전 보고서 Word 초안, Word→PDF 변환 / 그래프·촬영 요청서는 "
                      f"미구현 ({REASON})"}
