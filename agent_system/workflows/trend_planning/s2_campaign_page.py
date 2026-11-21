"""메인 시나리오 2단계 — 기획전 페이지 틀 작성 (원본 11~16단계). Market → Content → Validation → Reporting.

리뷰·소구점 → 문구·HTML 초안 → 검증 → 마케터 페이지 확정
콘텐츠 생성·검증 Agent 구현 전이라 지금은 14단계에서 멈춥니다.
"""

from agent_system.workflows.base import Stage, Step

STAGE = Stage(
    key="s2_campaign_page",
    title="기획전 페이지 틀 작성",
    requires=("category", "products"),
    steps=(
        Step(11, "경쟁 유사 상품 리뷰·판매 포인트", owner="market_analysis", instruction=(
            "주력 상품 '{products}'(카테고리 '{category}')의 주력 키워드가 상품명에 들어간 무신사 상품을 유사 상품으로 보고, "
            "리뷰 항목 언급률·불만 비율과 판매 포인트(소재·관리·핏·색 등) 빈도를 집계해 주세요.")),
        Step(12, "주제·소구점 후보와 빠진 상품 정보", owner="reporting", optional=True, instruction=(
            "주제·소구점 후보와, 상품별 detail_desc와 대조해 빠진 정보, 상품 노출 순서를 정리해 주세요.\n{previous}")),
        Step(13, "공통 소구점·가격·혜택 입력", kind="select", owner="marketer", selects="selling_points", instruction=(
            "공통 소구점 2~3개를 골라 주시고 상품별 실제 값(가격·할인율·기간)과 노출 순서를 알려 주세요. "
            "(예: \"소구점은 보풀 적은 소재, 부드러운 촉감으로 확정\")")),
        Step(14, "대표 문구·상품 카드·페이지 틀 후보", owner="content_generation", instruction=(
            "확정 내용(소구점 '{selling_points}', 주력 상품 '{products}')만으로 대표 문구 후보 2~3개와 상품 카드 문구를 쓰고 "
            "기획전 템플릿에 채워 페이지 틀 후보를 만들어 주세요.")),
        Step(15, "페이지 틀 근거 검사", owner="validation", instruction=(
            "페이지 틀 후보의 소재명·수치·기능 표현을 입력값과 대조하고 금지 표현·빈칸을 표시해 주세요.\n{previous}")),
        Step(16, "페이지 틀 1차 확정", kind="select", owner="marketer", selects="page", instruction=(
            "페이지 틀 후보 중 하나를 골라 주세요. (예: \"페이지는 2번 후보로 확정\")")),
    ),
)
