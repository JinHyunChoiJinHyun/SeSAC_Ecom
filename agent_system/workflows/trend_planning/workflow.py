"""메인 시나리오 — 트렌드 기반 기획전 (원본 1~29단계). 4개 단계를 순서대로 연결합니다.

    s1 주력 상품 선정 → s2 기획전 페이지 틀 → s3 타깃·발송 준비 → s4 실행 계획 보고·승인
마케터 선택 단계(select)마다 멈추고, 확정값 승인 후 다음 단계로 진행합니다.
"""

from agent_system.workflows.base import Workflow
from agent_system.workflows.trend_planning import s1_product_selection, s2_campaign_page, s3_targeting, s4_report_execution

WORKFLOW = Workflow(
    name="trend_planning",
    title="트렌드 기반 기획전",
    description=(
        "기획전을 처음부터 준비하는 업무 흐름: 관심 상승 카테고리 → 키워드 → 주력 상품 선정 → 페이지 틀 → 타깃·메시지 → "
        "실행 계획 보고·승인. 마케터가 기획전 준비 전체 또는 '주력 상품 찾는 것부터' 진행해 달라고 할 때 시작합니다."
    ),
    examples=("3주 뒤부터 4주간 기획전 여는데 주력 상품 찾아줘", "가을 니트 기획전 준비 시작하자",
              "기획전 준비 워크플로 시작해줘"),
    stages=(s1_product_selection.STAGE, s2_campaign_page.STAGE, s3_targeting.STAGE, s4_report_execution.STAGE),
)
