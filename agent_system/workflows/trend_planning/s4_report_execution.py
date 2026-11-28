"""메인 시나리오 4단계 — 실행 계획 보고와 승인 요청 (원본 23~29단계). Reporting → Validation → Execution.

보고서 → 마케터 승인 → 팀장 승인 → 페이지 전달 → URL 등록·확인 → 기획전 시작일 발송 → 사후 확인 예약
아키텍처 원칙 6: 채팅의 "승인할게"는 승인이 아니라 승인 버튼을 띄우는 신호입니다.
팀장 승인(26단계)은 승인 API 기록으로만 판정하는 gate라 지금은 멈춥니다.
"""

from agent_system.workflows.base import Stage, Step

STAGE = Stage(
    key="s4_report_execution",
    title="실행 계획 보고 및 승인 요청",
    requires=("products", "page", "target"),
    steps=(
        Step(23, "실행 계획 보고서 작성", owner="reporting", instruction=(
            "상품 선정·페이지·타깃 결과를 종합해 실행 계획 보고서를 작성하고, 보고서 속 수치·문구도 근거 검사해 주세요.")),
        Step(24, "보고서 확인 후 팀장 승인 요청 지시", kind="select", owner="marketer", selects="report", instruction=(
            "보고서를 확인하셨으면 팀장 승인 요청을 지시해 주세요. (예: \"보고서 확인 완료로 확정\")")),
        Step(25, "팀장 승인 요청 메일 발송", owner="campaign_execution", instruction=(
            "팀장에게 승인 요청 메일(보고서 링크 첨부)을 보내고 상태를 '승인 대기'로 기록해 주세요.")),
        Step(26, "팀장 승인", kind="gate", owner="team_lead", instruction="팀장 승인·수정 요청·반려(사유 기재)를 기다립니다."),
        Step(27, "승인 페이지 틀 전달", owner="campaign_execution", instruction=(
            "승인 확인 후 승인된 페이지 틀 파일(HTML)을 마케터에게 메일로 전달해 주세요.")),
        Step(28, "페이지 URL 등록", kind="select", owner="marketer", selects="page_url", instruction=(
            "이미지 교체 후 사내 페이지 관리 도구에 올린 페이지 URL을 알려 주세요.")),
        Step(29, "URL 확인·발송·사후 확인 예약", owner="campaign_execution", instruction=(
            "기획전 시작일에 등록된 URL이 열리는지 확인하고, 문구에 URL을 넣어 발송·결과 보고·사후 확인을 예약해 주세요.")),
    ),
)
