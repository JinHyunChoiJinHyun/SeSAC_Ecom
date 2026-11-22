"""메인 시나리오 2단계 — 기획전 페이지 틀 작성 (원본 11~16단계). Market → Content → Validation → Reporting.

리뷰·소구점 → 문구·HTML 초안 → 검증 → 마케터 페이지 확정
콘텐츠 생성·검증 Agent 구현 전이라 지금은 14단계에서 멈춥니다.
"""

from agent_system.workflows.base import Stage, Step

# CART_SIGNAL_DEMO_OUTER_V1
STAGE = Stage(
    key="s2_campaign_page",
    title="기획전 페이지 틀 작성",
    requires=("category", "products"),
    steps=(
        Step(11, "경쟁 유사 상품 리뷰·판매 포인트", owner="market_analysis", instruction=(
            "기획전 카테고리 '{category}'의 무신사 수집 랭킹 상품·리뷰 전체를 분석하세요(상품명 키워드 필터를 필수로 두지 않음). "
            "리뷰 항목 언급률·불만 비율과 판매 포인트(소재·관리·핏·색 등) 빈도를 집계하고, 무신사 결과가 H&M 상품 직접 리뷰가 아님을 표시하세요.")),
        Step(12, "주제·소구점 후보와 빠진 상품 정보", owner="reporting", instruction=(
            "주제·소구점 후보와, 상품별 detail_desc와 대조해 빠진 정보, 상품 노출 순서를 정리해 주세요.\n{previous}")),
        Step(13, "공통 소구점·가격·혜택 입력", kind="select", owner="marketer", selects="selling_points", instruction=(
            "검증된 소구점 중 원하는 개수를 골라 주시고 (2~3개 권장, 상품별 한정 특성은 해당 상품에만 적용) 상품별 실제 값(가격·할인율·기간)과 노출 순서를 알려 주세요. "
            "12단계 추천 번호와 항목별 입력 형식을 사용하세요.")),
        Step(14, "대표 문구·상품 카드·페이지 틀 후보", owner="content_generation", instruction=(
            "확정 내용(소구점 '{selling_points}', 주력 상품 '{products}')만으로 대표 문구 후보 2~3개와 상품 카드 문구를 쓰고 "
            "기획전 템플릿에 채워 페이지 틀 후보를 만들어 주세요.")),
        Step(15, "페이지 틀 근거 검사", owner="validation", instruction=(
            "페이지 틀 후보의 소재명·수치·기능 표현을 입력값과 대조하고 금지 표현·빈칸을 표시해 주세요.\n{previous}")),
        Step(16, "페이지 틀 1차 확정", kind="select", owner="marketer", selects="page", instruction=(
            "페이지 틀 후보 중 하나를 골라 주세요. (예: \"페이지는 2번 후보로 확정\")")),
    ),
)
