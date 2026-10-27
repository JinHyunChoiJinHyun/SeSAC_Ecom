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


# =========================================================
# Supervisor 출력 구조
# =========================================================

class RouteDecision(BaseModel):

    next_agent: Literal[
        "data_agent",
        "prep_agent",
        "model_agent",
        "experiment_agent",
        "visualization_agent",
        "final_response",
    ] = Field(
        description="다음으로 실행할 Agent"
    )

    reason: str = Field(
        description="해당 Agent를 선택한 이유"
    )


# =========================================================
# LLM 연결
# =========================================================

llm = get_openai_chat_client()


# =========================================================
# 사용자가 어떤 전문 Agent 작업을 요청했는지 확인
# =========================================================

def detect_requested_agents(
    user_query: str
) -> dict:
    """
    사용자 질문에

    - 전처리 / 데이터 품질 검사
    - 모델링
    - 통계 실험
    - 시각화

    요청이 실제로 포함되어 있는지 확인합니다.
    """

    query = (
        user_query
        .lower()
    )


    # =====================================================
    # Prep Agent 키워드
    # =====================================================

    prep_keywords = [
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
    ]


    # =====================================================
    # Model Agent 키워드
    # =====================================================

    model_keywords = [
        "예측",
        "모델",
        "모델링",
        "학습",
        "분류",
        "회귀",
        "구매 예측",
        "이탈 예측",
        "churn",
        "prediction",
    ]


    # =====================================================
    # Experiment Agent 키워드
    # =====================================================

    experiment_keywords = [
        "통계 검정",
        "가설 검정",
        "유의성",
        "p-value",
        "pvalue",
        "p value",
        "신뢰구간",
        "a/b",
        "ab test",
        "a/b test",
        "실험 분석",
    ]


    # =====================================================
    # Visualization Agent 키워드
    # =====================================================
    #
    # 중요:
    #
    # 사용자가
    #
    # "상관관계를 보여줘"
    #
    # 라고 요청할 때
    # 반드시 Visualization Agent로 갈 수 있도록
    # 상관관계 관련 표현도 포함합니다.
    # =====================================================

    visualization_keywords = [
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

        # ---------------------------------------------
        # 상관관계 요청도 시각화 요청으로 처리
        # ---------------------------------------------
        "상관관계",
        "상관 관계",
        "상관계수",
        "상관 계수",
        "correlation",
        "correlation matrix",
        "corr",
    ]


    return {

        "prep_agent":
            any(
                keyword in query
                for keyword
                in prep_keywords
            ),

        "model_agent":
            any(
                keyword in query
                for keyword
                in model_keywords
            ),

        "experiment_agent":
            any(
                keyword in query
                for keyword
                in experiment_keywords
            ),

        "visualization_agent":
            any(
                keyword in query
                for keyword
                in visualization_keywords
            ),
    }


# =========================================================
# Supervisor Routing
# =========================================================

def route_request(
    user_query: str,
    completed_steps: list[str],
    has_dataframe: bool,
    has_processed_dataframe: bool,
    has_model_result: bool,
    has_experiment_result: bool,
    has_visualization_result: bool,
):

    if llm is None:
        return None


    requested_agents = (
        detect_requested_agents(
            user_query
        )
    )


    # =====================================================
    # 이미 완료된 Agent는 다시 실행하지 않도록 차단
    # =====================================================

    blocked_agents = []


    if (
        "data"
        in completed_steps

        and has_dataframe
    ):

        blocked_agents.append(
            "data_agent"
        )


    if (
        "prep"
        in completed_steps

        and has_processed_dataframe
    ):

        blocked_agents.append(
            "prep_agent"
        )


    if (
        "model"
        in completed_steps

        and has_model_result
    ):

        blocked_agents.append(
            "model_agent"
        )


    if (
        "experiment"
        in completed_steps

        and has_experiment_result
    ):

        blocked_agents.append(
            "experiment_agent"
        )


    if (
        "visualization"
        in completed_steps

        and has_visualization_result
    ):

        blocked_agents.append(
            "visualization_agent"
        )


    try:

        router = (
            llm.with_structured_output(
                RouteDecision
            )
        )


        prompt = (
            ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """
당신은 Synerise 멀티 AI Agent 시스템의
Supervisor Agent입니다.


========================================
[역할]
========================================

사용자의 원래 요청과
현재까지 완료된 작업,
현재 State를 확인하고

다음으로 실행할 Agent 하나를 선택합니다.


========================================
[Agent 역할]
========================================

data_agent

DuckDB에서 필요한 데이터를
조회하기 위한 SQL을 생성합니다.


prep_agent

사용자가 다음 작업을 요청했을 때 사용합니다.

- 전처리
- 결측치 검사
- 중복 검사
- timestamp 처리
- 데이터 품질 검사
- Feature 전처리

DataFrame이 있다는 이유만으로
prep_agent를 선택하면 안 됩니다.


visualization_agent

사용자가 다음 작업을 요청했을 때 사용합니다.

- 그래프
- 차트
- 시각화
- 분포 시각화
- 시간 추이 시각화
- 상관관계
- 상관계수
- correlation
- heatmap


특히 사용자가

"상관관계를 보여줘"

"상관계수를 보여줘"

처럼 명시적으로
그래프나 시각화라는 단어를 쓰지 않더라도

여러 숫자형 지표 사이의
상관관계를 요청했다면

Visualization Agent 작업으로
판단하십시오.


Visualization Agent는 이미 준비된
DataFrame 또는 processed_dataframe을
사용하여 차트를 생성합니다.


model_agent

예측, 모델링, 분류, 회귀 등을 담당합니다.


experiment_agent

통계 검정, 가설 검정,
p-value, 신뢰구간,
A/B Test 등을 담당합니다.


final_response

사용자가 요청한 모든 작업이 완료되었을 때
최종 결과를 전달합니다.


========================================
[Routing 규칙]
========================================

1.

사용자의 요청에 필요한 DataFrame이 없다면
먼저 data_agent를 선택하십시오.


2.

단순 데이터 조회만 요청했고
data 작업이 완료되었다면

final_response를 선택하십시오.


3.

사용자가 전처리나 데이터 품질 검사를
명시적으로 요청했고

DataFrame이 준비되어 있으며
prep 작업이 아직 완료되지 않았다면

prep_agent를 선택하십시오.


4.

사용자가 시각화를 명시적으로 요청했고

시각화에 사용할 DataFrame이 준비되어 있으며
visualization 작업이 아직 완료되지 않았다면

visualization_agent를 선택하십시오.


여기서 시각화 요청에는 다음도 포함됩니다.

- 상관관계
- 상관 관계
- 상관계수
- correlation
- correlation matrix
- heatmap


따라서

"방문, 검색, 장바구니 추가,
장바구니 삭제, 구매 횟수 간의
상관관계를 보여줘"

와 같은 요청은

Data 완료 후
Visualization Agent를 실행해야 합니다.


5.

사용자가

"조회해서 전처리하고 그래프로 보여줘"

처럼 여러 작업을 요청했다면

아직 완료되지 않은 요청 중
현재 데이터 상태에서 실행 가능한
다음 Agent를 선택하십시오.


예:

Data 완료
→ Prep 필요
→ Visualization 필요

라면 Prep을 먼저 실행한 뒤
다음 Supervisor에서
Visualization을 선택할 수 있습니다.


6.

Data → Prep → Model → Visualization 순서를
기계적으로 실행하지 마십시오.

사용자의 실제 요청에 필요한 Agent만
선택하십시오.


========================================
[완료 Agent 재실행 금지]
========================================

blocked_agents에 포함된 Agent는
이미 완료된 작업입니다.

절대로 다시 선택하지 마십시오.


========================================
[예시 1]
========================================

사용자:

"구매 횟수가 많은 고객 10명을 가져와줘"


실행:

Data
→ Final


========================================
[예시 2]
========================================

사용자:

"구매 횟수가 많은 고객 10명을 가져와서
결측치와 중복을 확인해줘"


실행:

Data
→ Prep
→ Final


========================================
[예시 3]
========================================

사용자:

"구매 횟수가 많은 고객 10명을
막대그래프로 보여줘"


실행:

Data
→ Visualization
→ Final


========================================
[예시 4]
========================================

사용자:

"데이터를 전처리한 뒤
막대그래프로 보여줘"


실행:

Data
→ Prep
→ Visualization
→ Final


========================================
[예시 5]
========================================

사용자:

"고객별 방문, 검색,
장바구니 추가,
장바구니 삭제,
구매 횟수 간의
상관관계를 보여줘"


실행:

Data
→ Visualization
→ Final


========================================
[최종 규칙]
========================================

사용자의 요청이 모두 완료되었다면
반드시 final_response를 선택하십시오.
"""
                    ),

                    (
                        "user",
                        """
사용자 요청:

{user_query}


완료된 작업:

{completed_steps}


사용자가 요청한 전문 Agent:

{requested_agents}


다시 선택하면 안 되는 Agent:

{blocked_agents}


현재 State:

dataframe 존재:
{has_dataframe}

processed_dataframe 존재:
{has_processed_dataframe}

model_result 존재:
{has_model_result}

experiment_result 존재:
{has_experiment_result}

visualization_result 존재:
{has_visualization_result}


다음 Agent 하나만 선택하십시오.
"""
                    )
                ]
            )
        )


        chain = (
            prompt
            | router
        )


        decision = chain.invoke(
            {
                "user_query":
                    user_query,

                "completed_steps":
                    completed_steps,

                "requested_agents":
                    requested_agents,

                "blocked_agents":
                    blocked_agents,

                "has_dataframe":
                    has_dataframe,

                "has_processed_dataframe":
                    has_processed_dataframe,

                "has_model_result":
                    has_model_result,

                "has_experiment_result":
                    has_experiment_result,

                "has_visualization_result":
                    has_visualization_result,
            }
        )


        # =================================================
        # DataFrame이 없는데 전문 Agent를 선택한 경우
        # =================================================

        if (
            not has_dataframe

            and decision.next_agent
            in {
                "prep_agent",
                "model_agent",
                "experiment_agent",
                "visualization_agent",
            }
        ):

            decision = RouteDecision(

                next_agent=
                    "data_agent",

                reason=(
                    "전문 Agent 작업에 필요한 DataFrame이 "
                    "아직 없으므로 먼저 data_agent를 실행합니다."
                )
            )


        # =================================================
        # 사용자가 명시적으로 요청한 Prep이 남아 있으면 보장
        # =================================================

        if (
            has_dataframe

            and requested_agents[
                "prep_agent"
            ]

            and "prep"
            not in completed_steps
        ):

            decision = RouteDecision(

                next_agent=
                    "prep_agent",

                reason=(
                    "사용자가 전처리 또는 데이터 품질 검사를 "
                    "요청했고 아직 prep 작업이 완료되지 않았습니다."
                )
            )


        # =================================================
        # Prep 요청이 없거나 완료된 뒤
        # Visualization 요청 보장
        # =================================================

        elif (
            has_dataframe

            and requested_agents[
                "visualization_agent"
            ]

            and "visualization"
            not in completed_steps
        ):

            decision = RouteDecision(

                next_agent=
                    "visualization_agent",

                reason=(
                    "사용자가 시각화 또는 상관관계 분석을 요청했고 "
                    "시각화할 DataFrame이 준비되어 있습니다."
                )
            )


        # =================================================
        # 단순 조회 완료
        # =================================================

        no_special_agent_requested = (

            not requested_agents[
                "prep_agent"
            ]

            and not requested_agents[
                "model_agent"
            ]

            and not requested_agents[
                "experiment_agent"
            ]

            and not requested_agents[
                "visualization_agent"
            ]
        )


        if (
            has_dataframe

            and "data"
            in completed_steps

            and no_special_agent_requested
        ):

            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    "사용자는 단순 데이터 조회만 요청했고 "
                    "data 작업이 완료되었으므로 "
                    "추가 Agent 없이 최종 결과를 반환합니다."
                )
            )


        # =================================================
        # 요청하지 않은 Agent 호출 방지
        # =================================================

        if (
            decision.next_agent
            == "prep_agent"

            and not requested_agents[
                "prep_agent"
            ]
        ):

            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    "사용자가 전처리를 요청하지 않았으므로 "
                    "Prep Agent를 실행하지 않습니다."
                )
            )


        elif (
            decision.next_agent
            == "model_agent"

            and not requested_agents[
                "model_agent"
            ]
        ):

            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    "사용자가 모델링을 요청하지 않았으므로 "
                    "Model Agent를 실행하지 않습니다."
                )
            )


        elif (
            decision.next_agent
            == "experiment_agent"

            and not requested_agents[
                "experiment_agent"
            ]
        ):

            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    "사용자가 통계 검정을 요청하지 않았으므로 "
                    "Experiment Agent를 실행하지 않습니다."
                )
            )


        elif (
            decision.next_agent
            == "visualization_agent"

            and not requested_agents[
                "visualization_agent"
            ]
        ):

            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    "사용자가 시각화 또는 상관관계 분석을 "
                    "요청하지 않았으므로 "
                    "Visualization Agent를 실행하지 않습니다."
                )
            )


        # =================================================
        # 이미 완료된 Agent 재실행 방지
        # =================================================

        if (
            decision.next_agent
            in blocked_agents
        ):

            selected_agent = (
                decision.next_agent
            )


            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    f"{selected_agent}는 이미 완료된 작업이므로 "
                    "다시 실행하지 않고 결과를 반환합니다."
                )
            )


        # =================================================
        # 아직 구현되지 않은 Agent 처리
        # =================================================

        if (
            decision.next_agent
            not in IMPLEMENTED_AGENTS
        ):

            requested_agent = (
                decision.next_agent
            )


            decision = RouteDecision(

                next_agent=
                    "final_response",

                reason=(
                    f"{requested_agent}가 필요한 요청이지만 "
                    "아직 해당 Agent가 구현되지 않았으므로 "
                    "현재까지 완료된 결과를 반환합니다."
                )
            )


    except Exception as e:

        print(
            f"[Supervisor 오류] "
            f"{repr(e)}"
        )

        decision = None


    return decision