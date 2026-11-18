"""서브 시나리오 — 트렌드를 반영한 하프집업 니트 촬영·콘텐츠 기획 (4단계).

새 run으로 시작하고 parent_run_id = 메인 run (같은 thread에 메인 run이 있으면 연결).
메인 확정값(카테고리·주력 상품)을 이어받습니다. 마케터 제공까지이며 승인·발송은 없습니다.
"""

from agent_system.workflows.base import Stage, Step, Workflow

STAGE = Stage(
    key="photography",
    title="촬영·콘텐츠 기획",
    steps=(
        Step(1, "트렌드 분석 및 촬영 방향 제안", owner="market_analysis", instruction=(
            "요청: {request}\n셀럽 착장과 쇼핑 검색 동향을 분석해 기획전에 활용할 스타일링과 촬영 방향 근거를 제시해 주세요. "
            "(카테고리 '{category}', 주력 상품 '{products}')")),
        Step(2, "구매 기록 기반 대표 상품 비교", owner="product_analysis", instruction=(
            "주력 상품 '{products}' 후보의 온라인 구매 기록을 같은 기간·조건으로 비교해 첫 화면에 둘 상품 후보를 제안해 주세요.")),
        Step(2, "첫 화면 상품 선택", kind="select", owner="marketer", selects="first_screen", instruction=(
            "기획전 첫 화면에 배치할 상품을 골라 주세요. (예: \"첫 화면 상품은 0100000001로 확정\")")),
        Step(3, "리뷰 기반 촬영 우선순위", owner="market_analysis", instruction=(
            "유사 상품 리뷰에서 고객이 자주 확인하는 항목(사이즈·두께감·색감 등)의 언급률을 정리해 주세요.")),
        Step(3, "필수 컷·추가 컷 기획", owner="content_generation", instruction=(
            "첫 화면 상품 '{first_screen}'과 리뷰 항목으로 촬영 시간에 맞춰 필수 컷과 추가 컷의 순서를 정해 주세요.\n{previous}")),
        Step(4, "촬영 요청서 작성", owner="reporting", instruction=(
            "대표 상품 선정 근거, 컷별 촬영 포인트, 추가 확인 사항을 팀장·MD·촬영팀이 한 장에서 볼 수 있게 정리해 주세요.\n{previous}")),
    ),
)

WORKFLOW = Workflow(
    name="photography_planning",
    title="촬영·콘텐츠 기획",
    description=(
        "확정된 주력 상품으로 촬영·콘텐츠를 기획하는 흐름: 트렌드·촬영 방향 → 첫 화면 상품 선정 → 리뷰 기반 촬영 컷 → "
        "촬영 요청서. 마케터가 촬영 기획·촬영 요청서 준비를 진행해 달라고 할 때 시작합니다."
    ),
    examples=("하프집업 니트 촬영 기획 시작하자", "촬영 요청서 준비 진행해줘"),
    stages=(STAGE,),
    requires=("products",),
    parent="trend_planning",
)
