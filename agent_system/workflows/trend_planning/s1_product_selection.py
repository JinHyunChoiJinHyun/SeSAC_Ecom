"""메인 시나리오 1단계 — 주력 상품 선정 (원본 1~10단계). Market → Product → Reporting.

트렌드·키워드 평가 → 상품 매칭 → 후보 시각화 → 마케터 상품 확정
1단계(마케터 요청)는 워크플로를 시작한 메시지 자체입니다 (run["request"]).

아키텍처 우선 적용
- 5단계: 시나리오는 "H&M 속성·스타일 태그 + 무신사 상품명으로 키워드 후보"지만, 아키텍처상 상품 분석은 H&M(DuckDB)만
  읽습니다. 그래서 5단계는 H&M 속성·스타일 태그로 후보를 만들고, 무신사 근거는 6단계(시장 분석)에서 봅니다.
- 3·7·9단계 시각화는 결과 정리 Agent 담당. 구현 전에는 건너뛰고 각 분석 결과 표로 대신합니다 (optional).
"""

from agent_system.workflows.base import Stage, Step

STAGE = Stage(
    key="s1_product_selection",
    title="주력 상품 선정",
    steps=(
        Step(2, "관심 상승 카테고리 찾기", owner="market_analysis", instruction=(
            "기획전 요청: {request}\n"
            "최신 배치 스냅샷에서 현재 관심이 오르는 패션 카테고리를 찾고, 기획전 종료일까지 이어질 근거를 계산해 "
            "후보 카테고리 2~3개를 제안해 주세요. 데이터가 미연결이면 그 사실만 밝혀 주세요.")),
        Step(3, "카테고리 추이·선정 근거 시각화", owner="reporting", optional=True, instruction=(
            "직전 단계 결과로 카테고리 추이 그래프와 선정 근거·지속 근거를 정리해 주세요.\n{previous}")),
        Step(4, "기획전 카테고리 선택", kind="select", owner="marketer", selects="category", instruction=(
            "기획전 카테고리를 하나 골라 주세요. (예: \"카테고리는 니트로 확정\")\n"
            "시장 데이터가 미연결이면 원하는 카테고리를 직접 말씀해 주세요.")),
        Step(5, "카테고리 키워드 후보 만들기", owner="product_analysis", instruction=(
            "확정 카테고리 '{category}'의 H&M 상품을 검색해 스타일 태그·속성 분포를 보고, "
            "트렌드 확인에 쓸 키워드 후보(예: '하프집업 {category}')를 만들어 주세요. 각 키워드의 근거(태그·상품 수)를 함께 적어 주세요."),
             note="시나리오의 무신사 상품명 키워드는 아키텍처상 시장 분석(6단계)에서 확인"),
        Step(6, "키워드 상승률·지속 근거", owner="market_analysis", instruction=(
            "카테고리 '{category}' 기준어와 함께 아래 키워드 후보의 상대 증가율과 기획전 기간까지의 지속 근거를 계산해 주세요. "
            "유튜브는 보조 근거입니다.\n[키워드 후보]\n{previous}")),
        Step(7, "키워드 주력 후보·참고·제외 분류", owner="reporting", optional=True, instruction=(
            "직전 단계 결과로 키워드를 주력 후보·참고·제외로 나누고 비교 그래프를 만들어 주세요.\n{previous}")),
        Step(8, "키워드에 맞는 H&M 상품 찾기", owner="product_analysis", instruction=(
            "카테고리 '{category}'에서 아래 결과의 주력 후보 키워드(없으면 키워드 후보 전체)의 스타일 태그·속성과 맞는 "
            "H&M 상품을 찾고, 같은 기간·조건의 온라인 구매 실적과 함께 후보를 비교해 주세요.\n{previous}")),
        Step(9, "상품 후보 시각화", owner="reporting", optional=True, instruction=(
            "상품 후보와 키워드 일치 개수, 상품 이미지를 정리해 시각화해 주세요.\n{previous}")),
        Step(10, "최종 주력 상품 결정", kind="select", owner="marketer", selects="products", instruction=(
            "후보 중 기획전 주력 상품을 정해 주세요. (예: \"주력 상품은 0100000001, 0100000002로 확정\")")),
    ),
)
