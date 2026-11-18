"""⑥ Reporting Agent Tool 4개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    visualize_analysis_results   트렌드·상품 비교·타깃 결과 그래프 생성
    generate_campaign_report     기획전 실행 계획 보고서 생성 (메인)
    generate_photography_brief   촬영 우선순위·구도·상품 정보가 담긴 한 장짜리 촬영 요청서 생성 (서브)
    export_report_pdf            보고서 및 촬영 요청서 PDF 출력

백엔드 API (아키텍처 [3], scope report:write)
    POST /reports/charts (analysis_snapshot·target_score)   POST /reports/campaign (content_hash·model_version 표기)
    POST /reports/photography-brief (templates/reports/)     POST /reports/{report_id}/pdf
로컬 모드: 미구현 (분석 근거 스냅샷·보고서 템플릿은 백엔드 담당).
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.tools.common import backend_or_unimplemented, clean_id, missing_id

SOURCE = "보고서·시각화 (백엔드 report_service)"
REASON = "백엔드 /reports API(분석 스냅샷·보고서 템플릿) 연결 전"


def visualize_analysis_results(run_id: str | None = None, snapshot_ids: list[str] | None = None,
                               chart_type: str | None = None) -> dict:
    """트렌드·상품 비교·타깃 결과 그래프 생성 (워크플로 run의 분석 근거 스냅샷 기준)."""
    return backend_or_unimplemented("POST", "/reports/charts", source=SOURCE, reason=REASON, body={
        "run_id": clean_id(run_id), "snapshot_ids": list(snapshot_ids or []), "chart_type": chart_type})


def generate_campaign_report(campaign_id: str) -> dict:
    """기획전 실행 계획 보고서 생성 (상품 선정·페이지·타깃 결과 종합)."""
    return backend_or_unimplemented("POST", "/reports/campaign", source=SOURCE, reason=REASON,
                                    body={"campaign_id": clean_id(campaign_id)})


def generate_photography_brief(campaign_id: str, run_id: str | None = None) -> dict:
    """대표 상품 선정 근거, 컷별 촬영 포인트·구도, 추가 확인 사항을 한 장으로 정리한 촬영 요청서 생성."""
    return backend_or_unimplemented("POST", "/reports/photography-brief", source=SOURCE, reason=REASON,
                                    body={"campaign_id": clean_id(campaign_id), "run_id": clean_id(run_id)})


def export_report_pdf(report_id: str) -> dict:
    """보고서·촬영 요청서 PDF 출력."""
    report_id = clean_id(report_id)
    if not report_id and (blocked := missing_id(SOURCE, "report_id")):
        return blocked
    return backend_or_unimplemented("POST", f"/reports/{report_id}/pdf", source=SOURCE, reason=REASON)


TOOLS = {
    "visualize_analysis_results": visualize_analysis_results,
    "generate_campaign_report": generate_campaign_report,
    "generate_photography_brief": generate_photography_brief,
    "export_report_pdf": export_report_pdf,
}
TOOL_SOURCE = {name: "report" for name in TOOLS}


def data_status() -> dict:
    return {"report": "백엔드 API로 생성" if backend_client.enabled() else f"미구현 ({REASON})"}
