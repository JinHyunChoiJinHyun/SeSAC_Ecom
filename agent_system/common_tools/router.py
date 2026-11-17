"""Orchestrator가 다음 실행 Agent를 고르는 Router.

LLM이 1차 판단을 하고, 아래 규칙으로 결과를 보정합니다.
- DataFrame이 없으면 data_agent 먼저
- 사용자가 명시적으로 요청한 전처리/시각화는 반드시 실행
- 요청하지 않은 Agent, 이미 완료된 Agent는 실행하지 않음
- 예측 모델 기능은 현재 제공하지 않으므로 예측 전용 요청은 안내 후 종료
"""

import re
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
)

from langchain_core.prompts import (
    ChatPromptTemplate,
)

from env.gpt_client import (
    get_openai_chat_client,
)


# =========================================================
# 현재 실제 구현된 Agent
# =========================================================

IMPLEMENTED_AGENTS = {
    "data_agent",
    "prep_agent",
    "visualization_agent",
    "final_response",
}

# final_response가 이 문구를 보고 "지원하지 않는 기능" 안내를 붙입니다.
UNSUPPORTED_PREDICTION_REASON = (
    "예측 모델 기능은 현재 제공하지 않습니다."
)


# =========================================================
# Router 출력 구조
# =========================================================

class RouteDecision(BaseModel):

    next_agent: Literal[
        "data_agent",
        "prep_agent",
        "visualization_agent",
        "final_response",
    ] = Field(
        description="다음으로 실행할 Agent"
    )

    reason: str = Field(
        description="해당 Agent를 선택한 이유"
    )


# =========================================================
# 사용자가 어떤 전문 Agent 작업을 요청했는지 확인
# =========================================================

PREP_KEYWORDS = [
    "전처리",
    "데이터 정제",
    "데이터 품질",
    "결측",
    "결측치",
    "누락값",
    "missing",
    "null",
    "nan",
    "중복",
    "duplicate",
    "timestamp 변환",
    "datetime 변환",
    "날짜 변환",
    "시간순 정렬",
    "무한대",
    "infinite",
    "inf 값",
    "파생 변수",
    "파생변수",
    "feature engineering",
    "피처 엔지니어링",
    "반복 횟수",
    "반복 이벤트",
    "반복 구매",
]

VISUALIZATION_KEYWORDS = [
    "시각화",
    "그래프",
    "차트",
    "plot",
    "막대그래프",
    "막대 그래프",
    "바 차트",
    "bar chart",
    "선그래프",
    "선 그래프",
    "line chart",
    "히스토그램",
    "histogram",
    "산점도",
    "scatter",
    "박스플롯",
    "boxplot",
    "상자그림",
    "히트맵",
    "heatmap",
    "분포를 보여",
    "추이를 보여",
    # 관계 / 관련성 / 연관성
    "관계를 보여",
    "관계를 알려",
    "관계 보여",
    "관계 알려",
    "관계 분석",
    "관련성",
    "연관성",
    "연관 관계",
    "relationship",
    # 상관관계
    "상관관계",
    "상관 관계",
    "상관계수",
    "상관 계수",
    "correlation",
    "corr",
]

# 학습된 예측 모델이 필요한 요청 (현재 미지원)
PREDICTION_KEYWORDS = [
    "예측해",
    "예측 해",
    "예측 모델",
    "예측모델",
    "구매확률",
    "구매 확률",
    "이탈 예측",
    "이탈예측",
    "churn",
    "prediction",
    "모델 성능",
    "모델 비교",
    "pr-auc",
    "roc-auc",
]


def detect_requested_agents(
    user_query: str
) -> dict:
    """사용자 질문에 전처리 / 시각화 요청이 실제로 포함되어 있는지 확인합니다."""

    query = str(user_query or "").lower()

    return {
        "prep_agent": any(keyword in query for keyword in PREP_KEYWORDS),
        "visualization_agent": any(keyword in query for keyword in VISUALIZATION_KEYWORDS),
    }


def is_prediction_only_request(
    user_query: str
) -> bool:
    """학습된 예측 모델이 있어야만 답할 수 있는 요청인지 확인합니다.

    "구매가 많은 고객", "판매 추이"처럼 실제 데이터 집계로 답할 수 있는 요청은
    예측 요청이 아닙니다.
    """

    query = str(user_query or "").lower()
    if not any(keyword in query for keyword in PREDICTION_KEYWORDS):
        return False

    # "예측"이 들어 있어도 데이터 조회·집계·시각화 요청이 함께 있으면 Data 경로로 처리합니다.
    has_data_request = bool(
        re.search(r"조회|집계|추이|비중|분포|몇\s*(명|개|건)|알려줘.*(수|비율)|보여줘", query)
    )
    return not has_data_request


ROUTER_SYSTEM_PROMPT = """
당신은 CART SIGNAL 멀티 Agent 시스템의 Router입니다.
데이터는 common/data_sources.py에 등록된 출처(예: H&M, Amazon)입니다.

========================================
[역할]
========================================

사용자의 원래 요청과 현재까지 완료된 작업, 현재 State를 보고
다음으로 실행할 Agent 하나를 선택합니다.

========================================
[Agent 역할]
========================================

data_agent
DuckDB에 연결된 데이터 출처에서 필요한 데이터를 조회하기 위한 SQL을 생성합니다.

prep_agent
사용자가 전처리, 결측치·중복 검사, 날짜 변환, 데이터 품질 검사, 파생 변수를
명시적으로 요청했을 때만 사용합니다.
DataFrame이 있다는 이유만으로 prep_agent를 선택하면 안 됩니다.

visualization_agent
그래프, 차트, 시각화, 분포·추이 시각화, 두 개 이상 숫자 지표의 관계,
관련성/연관성, 상관관계/상관계수/heatmap 요청에 사용합니다.
"상관관계를 보여줘"처럼 차트 이름이 없어도 숫자 지표 사이의 관계 요청이면 시각화 작업입니다.
구체적인 차트 종류는 Visualization Agent가 정합니다.

final_response
사용자가 요청한 모든 작업이 완료되었을 때 최종 결과를 전달합니다.

이 시스템에는 학습된 예측 모델이 없습니다. 예측 모델을 실행하는 Agent는 없습니다.

========================================
[Routing 규칙]
========================================

1. 필요한 DataFrame이 없다면 먼저 data_agent를 선택하십시오.
2. 단순 조회만 요청했고 data 작업이 완료되었다면 final_response를 선택하십시오.
3. 전처리를 명시적으로 요청했고 DataFrame이 있으며 prep이 아직 안 끝났다면 prep_agent.
4. 시각화/관계 분석을 요청했고 DataFrame이 있으며 visualization이 아직 안 끝났다면 visualization_agent.
5. 여러 작업을 요청했다면 아직 완료되지 않은 작업 중 지금 실행 가능한 다음 Agent를 고르십시오.
   (예: Data 완료 → Prep 필요 → Visualization 필요라면 Prep 먼저)
6. Data → Prep → Visualization 순서를 기계적으로 실행하지 말고 요청에 필요한 Agent만 고르십시오.

========================================
[완료 Agent 재실행 금지]
========================================

blocked_agents에 포함된 Agent는 이미 완료된 작업입니다. 다시 선택하지 마십시오.

========================================
[예시]
========================================

"상품 유형별 판매량 Top 10을 알려줘"          → Data → Final
"월별 온라인 판매 추이를 선그래프로 보여줘"     → Data → Visualization → Final
"니트 구매 고객 목록을 가져와서 결측치를 확인해줘" → Data → Prep → Final
"고객별 구매 횟수, 평균 가격지수, 나이의 상관관계를 보여줘" → Data → Visualization → Final
"Amazon 여성 스웨터 리뷰 별점 분포를 그래프로 보여줘" → Data → Visualization → Final

========================================
[최종 규칙]
========================================

요청이 모두 완료되었다면 반드시 final_response를 선택하십시오.
"""


ROUTER_HUMAN_PROMPT = """
사용자 요청:
{user_query}

완료된 작업:
{completed_steps}

키워드로 감지된 요청 Agent:
{requested_agents}

재실행 금지 Agent:
{blocked_agents}

현재 State:
- dataframe 있음: {has_dataframe}
- processed_dataframe 있음: {has_processed_dataframe}
- visualization 결과 있음: {has_visualization_result}

다음으로 실행할 Agent 하나를 선택하십시오.
"""


# =========================================================
# Router
# =========================================================

def route_request(
    user_query: str,
    completed_steps: list[str],
    has_dataframe: bool,
    has_processed_dataframe: bool,
    has_visualization_result: bool,
    model_config=None,
):

    requested_agents = detect_requested_agents(user_query)

    # =====================================================
    # 예측 모델이 필요한 요청: 데이터 조회 없이 안내
    # =====================================================

    if (
        not completed_steps
        and not requested_agents["visualization_agent"]
        and not requested_agents["prep_agent"]
        and is_prediction_only_request(user_query)
    ):
        return RouteDecision(
            next_agent="final_response",
            reason=UNSUPPORTED_PREDICTION_REASON,
        )

    llm = get_openai_chat_client(model_config=model_config)

    if llm is None:
        return None

    # =====================================================
    # 이미 완료된 Agent는 다시 실행하지 않도록 차단
    # =====================================================

    blocked_agents = []

    if "data" in completed_steps and has_dataframe:
        blocked_agents.append("data_agent")

    if "prep" in completed_steps and has_processed_dataframe:
        blocked_agents.append("prep_agent")

    if "visualization" in completed_steps and has_visualization_result:
        blocked_agents.append("visualization_agent")

    try:

        router = llm.with_structured_output(
            RouteDecision, method="function_calling"
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", ROUTER_SYSTEM_PROMPT),
                ("human", ROUTER_HUMAN_PROMPT),
            ]
        )

        chain = prompt | router

        decision = chain.invoke(
            {
                "user_query": user_query,
                "completed_steps": completed_steps,
                "requested_agents": requested_agents,
                "blocked_agents": blocked_agents,
                "has_dataframe": has_dataframe,
                "has_processed_dataframe": has_processed_dataframe,
                "has_visualization_result": has_visualization_result,
            }
        )

        # =================================================
        # DataFrame이 없는데 전문 Agent를 선택한 경우
        # =================================================

        if (
            not has_dataframe
            and decision.next_agent in {"prep_agent", "visualization_agent"}
        ):
            decision = RouteDecision(
                next_agent="data_agent",
                reason=(
                    "요청한 전문 작업에 필요한 DataFrame이 "
                    "아직 없으므로 먼저 data_agent를 실행합니다."
                ),
            )

        # =================================================
        # 사용자가 명시적으로 요청한 Prep이 남아 있으면 보장
        # =================================================

        if (
            has_dataframe
            and requested_agents["prep_agent"]
            and "prep" not in completed_steps
        ):
            decision = RouteDecision(
                next_agent="prep_agent",
                reason=(
                    "사용자가 전처리 또는 데이터 품질 검사를 "
                    "요청했고 아직 prep 작업이 완료되지 않았습니다."
                ),
            )

        # =================================================
        # Prep 요청이 없거나 완료된 뒤 Visualization 요청 보장
        # =================================================

        elif (
            has_dataframe
            and requested_agents["visualization_agent"]
            and "visualization" not in completed_steps
        ):
            decision = RouteDecision(
                next_agent="visualization_agent",
                reason=(
                    "사용자가 시각화, 관계 또는 상관관계 분석을 요청했고 "
                    "시각화할 DataFrame이 준비되어 있습니다."
                ),
            )

        # =================================================
        # 단순 조회 완료
        # =================================================

        no_special_agent_requested = (
            not requested_agents["prep_agent"]
            and not requested_agents["visualization_agent"]
        )

        if (
            has_dataframe
            and "data" in completed_steps
            and no_special_agent_requested
        ):
            decision = RouteDecision(
                next_agent="final_response",
                reason=(
                    "사용자는 단순 데이터 조회만 요청했고 "
                    "data 작업이 완료되었으므로 "
                    "추가 Agent 없이 최종 결과를 반환합니다."
                ),
            )

        # =================================================
        # 요청하지 않은 Agent 호출 방지
        # =================================================

        if (
            decision.next_agent == "prep_agent"
            and not requested_agents["prep_agent"]
        ):
            decision = RouteDecision(
                next_agent="final_response",
                reason="사용자가 전처리를 요청하지 않았으므로 Prep Agent를 실행하지 않습니다.",
            )

        elif (
            decision.next_agent == "visualization_agent"
            and not requested_agents["visualization_agent"]
        ):
            decision = RouteDecision(
                next_agent="final_response",
                reason=(
                    "사용자가 시각화, 관계 또는 상관관계 분석을 "
                    "요청하지 않았으므로 Visualization Agent를 실행하지 않습니다."
                ),
            )

        # =================================================
        # 이미 완료된 Agent 재실행 방지
        # =================================================

        if decision.next_agent in blocked_agents:
            selected_agent = decision.next_agent
            decision = RouteDecision(
                next_agent="final_response",
                reason=(
                    f"{selected_agent}는 이미 완료된 작업이므로 "
                    "다시 실행하지 않고 결과를 반환합니다."
                ),
            )

        # =================================================
        # 구현되지 않은 Agent 처리
        # =================================================

        if decision.next_agent not in IMPLEMENTED_AGENTS:
            requested_agent = decision.next_agent
            decision = RouteDecision(
                next_agent="final_response",
                reason=(
                    f"{requested_agent}가 필요한 요청이지만 "
                    "아직 해당 Agent가 구현되지 않았으므로 "
                    "현재까지 완료된 결과를 반환합니다."
                ),
            )

    except Exception as e:

        print(f"[Router 오류] {repr(e)}")
        decision = None

    return decision
