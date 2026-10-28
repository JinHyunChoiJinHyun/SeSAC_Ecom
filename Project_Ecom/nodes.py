import pandas as pd

from typing import Literal

from langgraph.graph import (
    END,
)

from langgraph.types import (
    Command,
)

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
)


from state import (
    AgentState,
)


from supervisor_agent import (
    route_request,
)


from data_agent import (
    data_agent,
)


from prep_agent import (
    prep_agent,
)


from visualization_agent import (
    visualization_agent,
)


from env.tools.data_tools import (
    validate_select_sql,
    execute_sql_to_dataframe,
)


from env.tools.prep_tools import (
    check_dataframe_info,
    check_missing_values,
    check_duplicates,
    convert_timestamp,
    sort_by_timestamp,
    create_repeat_count_feature,
    check_infinite_values,
    replace_infinite_with_nan,
    handle_feature_missing_values,
)


from env.tools.visualization_tools import (
    create_bar_chart,
    create_line_chart,
    create_histogram,
    create_boxplot,
    create_scatterplot,
    create_correlation_heatmap,
)


# =========================================================
# 완료 Step 추가
# =========================================================

def add_completed_step(
    state: AgentState,
    step: str
):

    completed_steps = list(
        state.get(
            "completed_steps",
            []
        )
    )


    if step not in completed_steps:

        completed_steps.append(
            step
        )


    return completed_steps


# =========================================================
# 분석용 DataFrame 가져오기
# =========================================================

def get_analysis_dataframe(
    state: AgentState
):

    """
    processed_dataframe이 존재하면 우선 사용하고,
    없으면 원본 dataframe을 사용합니다.
    """

    processed_df = state.get(
        "processed_dataframe"
    )


    if isinstance(
        processed_df,
        pd.DataFrame
    ):

        return processed_df


    return state.get(
        "dataframe"
    )


# =========================================================
# 사용자가 명시적으로 요청한 Prep 작업 보장
# =========================================================

def enforce_requested_prep_actions(
    question: str,
    actions: list[str]
):

    result = list(
        actions
    )


    question_lower = (
        question.lower()
    )


    missing_keywords = [
        "결측",
        "누락",
        "missing",
        "null",
        "nan",
    ]


    if any(
        keyword in question_lower
        for keyword in missing_keywords
    ):

        if (
            "check_missing_values"
            not in result
        ):

            result.append(
                "check_missing_values"
            )


    duplicate_keywords = [
        "중복",
        "duplicate",
    ]


    if any(
        keyword in question_lower
        for keyword in duplicate_keywords
    ):

        if (
            "check_duplicates"
            not in result
        ):

            result.append(
                "check_duplicates"
            )


    infinite_keywords = [
        "무한대",
        "infinite",
        "inf 확인",
    ]


    if any(
        keyword in question_lower
        for keyword in infinite_keywords
    ):

        if (
            "check_infinite_values"
            not in result
        ):

            result.append(
                "check_infinite_values"
            )


    return list(
        dict.fromkeys(
            result
        )
    )


# =========================================================
# 최종 응답에 검증용 SQL 추가
# =========================================================

def append_verification_sql(
    final_answer: str,
    state: AgentState,
) -> str:
    """
    Data Agent가 생성한 SQL이 존재하면
    최종 응답 마지막에 검증용 SQL을 추가합니다.
    """

    sql_query = state.get("sql_query")

    if not sql_query:
        return final_answer

    sql_query = str(sql_query).strip()

    if not sql_query:
        return final_answer

    data_purpose = state.get("data_purpose")

    final_answer += (
        "\n\n"
        "==============================\n"
        "[기반 데이터 검증 정보]\n"
        "==============================\n"
    )

    if data_purpose:
        final_answer += (
            "\n조회 목적:\n"
            f"{data_purpose}\n"
        )

    final_answer += (
        "\n사용된 SQL:\n"
        "```sql\n"
        f"{sql_query}\n"
        "```"
    )

    return final_answer


# =========================================================
# 대용량 시각화용 SQL 보정
# =========================================================

MAX_VISUAL_SAMPLE_ROWS = 100_000


def ensure_visual_sample_sql(
    sql_query: str,
    sample_size: int = MAX_VISUAL_SAMPLE_ROWS,
) -> str:
    """
    Data Agent가 visual_sample을 선택했지만
    SQL에 실제 샘플링을 넣지 않은 경우를 대비한 안전장치입니다.

    중요한 점:
    원본 raw 이벤트를 먼저 샘플링하지 않고,
    Data Agent가 만든 전체 집계/결합 SQL을 서브쿼리로 실행한 뒤
    그 최종 행 결과에만 DuckDB reservoir sampling을 적용합니다.
    """

    clean_sql = (
        str(sql_query)
        .strip()
        .rstrip(";")
    )

    upper_sql = clean_sql.upper()

    # 이미 Data Agent가 샘플링을 넣었다면 중복 적용하지 않습니다.
    if (
        "USING SAMPLE" in upper_sql
        or "TABLESAMPLE" in upper_sql
    ):
        return clean_sql + ";"

    safe_sample_size = max(
        1,
        min(
            int(sample_size or MAX_VISUAL_SAMPLE_ROWS),
            MAX_VISUAL_SAMPLE_ROWS,
        ),
    )

    return (
        "SELECT *\n"
        "FROM (\n"
        f"{clean_sql}\n"
        ") AS visual_source\n"
        f"USING SAMPLE reservoir({safe_sample_size} ROWS);"
    )


# =========================================================
# 관계 / 상관관계 요청의 데이터 범위 강제 보정
# =========================================================

RELATION_INTENT_KEYWORDS = [
    "관계",
    "상관관계",
    "상관 관계",
    "상관계수",
    "상관 계수",
    "관련성",
    "연관성",
    "correlation",
    "relationship",
]

BEHAVIOR_FEATURE_KEYWORDS = {
    "visit_count": [
        "방문",
        "페이지 방문",
        "visit",
    ],
    "search_count": [
        "검색",
        "search",
    ],
    "cart_add_count": [
        "장바구니 추가",
        "카트 추가",
        "add_to_cart",
        "cart add",
    ],
    "cart_remove_count": [
        "장바구니 삭제",
        "장바구니 제거",
        "카트 삭제",
        "카트 제거",
        "remove_from_cart",
        "cart remove",
    ],
    "purchase_count": [
        "구매",
        "purchase",
        "buy",
    ],
}


def detect_relation_features(
    question: str,
) -> list[str]:
    """
    사용자 질문이 관계/상관관계 요청인지 확인하고,
    질문에 명시된 고객 행동 Feature를 순서대로 반환합니다.

    현재 프로젝트에서 확인된 행동 지표만 대상으로 합니다.
    """

    question_lower = (
        str(question or "")
        .lower()
    )

    has_relation_intent = any(
        keyword in question_lower
        for keyword in RELATION_INTENT_KEYWORDS
    )

    if not has_relation_intent:
        return []

    features = []

    for feature_name, keywords in (
        BEHAVIOR_FEATURE_KEYWORDS.items()
    ):

        if any(
            keyword in question_lower
            for keyword in keywords
        ):

            features.append(
                feature_name
            )

    return features


def validate_relation_sql_features(
    sql_query: str,
    feature_columns: list[str],
) -> tuple[bool, list[str]]:
    """
    Data Agent가 만든 SQL에
    관계 분석에 필요한 Feature alias가 모두 있는지 확인합니다.
    """

    sql_lower = (
        str(sql_query or "")
        .lower()
    )

    missing_features = [
        column
        for column in feature_columns
        if column.lower() not in sql_lower
    ]

    return (
        len(missing_features) == 0,
        missing_features,
    )


def build_correlation_matrix_sql(
    sql_query: str,
    feature_columns: list[str],
) -> str:
    """
    Data Agent가 생성한 고객 단위 Feature SQL을
    전체 데이터 기준 correlation matrix SQL로 변환합니다.

    원본 raw 이벤트를 샘플링하지 않습니다.
    Data Agent SQL이 고객별 지표를 정확히 만든 뒤,
    DuckDB corr()가 그 전체 결과를 사용합니다.
    """

    if len(feature_columns) < 3:
        raise ValueError(
            "correlation matrix 생성에는 "
            "최소 3개의 Feature가 필요합니다."
        )

    clean_sql = (
        str(sql_query)
        .strip()
        .rstrip(";")
    )

    # 이미 corr()를 사용한 SQL이면 중복 변환하지 않습니다.
    if "corr(" in clean_sql.lower():
        return clean_sql + ";"

    pair_aliases = {}

    corr_expressions = []

    for left_index, left_column in enumerate(
        feature_columns
    ):

        for right_index in range(
            left_index + 1,
            len(feature_columns),
        ):

            right_column = (
                feature_columns[
                    right_index
                ]
            )

            alias = (
                f"corr_{left_index}_{right_index}"
            )

            pair_aliases[
                (
                    left_column,
                    right_column,
                )
            ] = alias

            pair_aliases[
                (
                    right_column,
                    left_column,
                )
            ] = alias

            corr_expressions.append(
                f"        corr({left_column}, {right_column}) "
                f"AS {alias}"
            )

    corr_select = ",\n".join(
        corr_expressions
    )

    row_queries = []

    for row_column in feature_columns:

        values = []

        for column in feature_columns:

            if column == row_column:

                value_expression = (
                    f"1.0 AS {column}"
                )

            else:

                alias = pair_aliases[
                    (
                        row_column,
                        column,
                    )
                ]

                value_expression = (
                    f"{alias} AS {column}"
                )

            values.append(
                f"    {value_expression}"
            )

        row_query = (
            "SELECT\n"
            f"    '{row_column}' AS feature,\n"
            + ",\n".join(
                values
            )
            + "\nFROM correlation_values"
        )

        row_queries.append(
            row_query
        )

    matrix_rows = (
        "\n\nUNION ALL\n\n"
        .join(
            row_queries
        )
    )

    return (
        "WITH customer_metrics AS (\n"
        "    SELECT *\n"
        "    FROM (\n"
        f"{clean_sql}\n"
        "    ) AS base_metrics\n"
        "),\n"
        "correlation_values AS (\n"
        "    SELECT\n"
        f"{corr_select}\n"
        "    FROM customer_metrics\n"
        ")\n"
        f"{matrix_rows};"
    )


# =========================================================
# Supervisor Node
# =========================================================

def supervisor_node(
    state: AgentState
) -> Command[
    Literal[
        "data_agent",
        "prep_agent",
        "model_agent",
        "experiment_agent",
        "visualization_agent",
        "final_response",
    ]
]:

    print(
        "[Supervisor Agent]"
    )


    completed_steps = (
        state.get(
            "completed_steps",
            []
        )
    )


    print(
        f"completed_steps = "
        f"{completed_steps}"
    )


    question = state.get(
        "question"
    )


    if not question:

        question = (
            state[
                "messages"
            ][-1]
            .content
        )


    decision = route_request(

        user_query=
            question,

        completed_steps=
            completed_steps,

        has_dataframe=
            isinstance(
                state.get(
                    "dataframe"
                ),
                pd.DataFrame
            ),

        has_processed_dataframe=
            isinstance(
                state.get(
                    "processed_dataframe"
                ),
                pd.DataFrame
            ),

        has_model_result=
            state.get(
                "model_result"
            ) is not None,

        has_experiment_result=
            state.get(
                "experiment_result"
            ) is not None,

        has_visualization_result=
            state.get(
                "visualization_result"
            ) is not None,
    )


    if decision is None:

        return Command(

            update={
                "question":
                    question,

                "error":
                    "Supervisor 실행 오류",
            },

            goto=
                "final_response",
        )


    print(
        f"next_agent = "
        f"{decision.next_agent}"
    )


    print(
        f"reason = "
        f"{decision.reason}"
    )


    return Command(

        update={
            "question":
                question,

            "next_agent":
                decision.next_agent,

            "supervisor_reason":
                decision.reason,
        },

        goto=
            decision.next_agent,
    )


# =========================================================
# Data Agent Node
# =========================================================

def data_node(
    state: AgentState
) -> Command[
    Literal[
        "sql_executor",
        "final_response",
    ]
]:

    print(
        "[Data Agent]"
    )


    if data_agent is None:

        return Command(

            update={
                "data_error":
                    "Data Agent 연결 실패",

                "error":
                    "Data Agent 연결 실패",
            },

            goto=
                "final_response",
        )


    question = (
        state[
            "question"
        ]
    )


    try:

        print(
            "[Data Agent invoke 시작]"
        )


        result = data_agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=
                            question
                    )
                ]
            },

            config={
                "recursion_limit": 30
            }
        )


        print(
            "[Data Agent invoke 완료]"
        )


        plan = result.get(
            "structured_response"
        )


        if plan is None:

            raise ValueError(
                "Data Agent가 SQL 계획을 "
                "반환하지 않았습니다."
            )


        sql_query = (
            plan.sql_query
            .strip()
        )


        purpose = (
            plan.purpose
            .strip()
        )


        # -------------------------------------------------
        # 데이터 범위 / 샘플링 메타데이터
        # -------------------------------------------------

        data_result_type = getattr(
            plan,
            "result_type",
            "full_result",
        )

        data_uses_full_population = bool(
            getattr(
                plan,
                "uses_full_population",
                True,
            )
        )

        data_is_sampled = bool(
            getattr(
                plan,
                "is_sampled",
                False,
            )
        )

        data_sample_size = int(
            getattr(
                plan,
                "sample_size",
                0,
            )
            or 0
        )

        data_sampling_reason = str(
            getattr(
                plan,
                "sampling_reason",
                "",
            )
            or ""
        ).strip()

        data_scope_note = str(
            getattr(
                plan,
                "scope_note",
                "",
            )
            or ""
        ).strip()


        # -------------------------------------------------
        # 관계 / 상관관계 요청은 LLM의 result_type보다
        # 질문에 명시된 Feature 수를 우선하여 강제 보정
        # -------------------------------------------------
        #
        # 2개 Feature 관계:
        #   전체 원천 데이터에서 고객 단위 지표를 계산한 뒤
        #   최종 행만 최대 100,000개 시각화 표본으로 사용
        #
        # 3개 이상 Feature 관계:
        #   전체 고객 단위 지표를 DuckDB corr()로 계산하고
        #   작은 correlation matrix만 pandas로 전달
        # -------------------------------------------------

        relation_features = (
            detect_relation_features(
                question
            )
        )

        if len(relation_features) >= 2:

            relation_sql_valid, missing_features = (
                validate_relation_sql_features(
                    sql_query=sql_query,
                    feature_columns=relation_features,
                )
            )

            if not relation_sql_valid:

                raise ValueError(
                    "관계 분석에 필요한 Feature가 "
                    "Data Agent SQL에서 누락되었습니다: "
                    f"{missing_features}"
                )


        if len(relation_features) >= 3:

            sql_query = (
                build_correlation_matrix_sql(
                    sql_query=sql_query,
                    feature_columns=relation_features,
                )
            )

            data_result_type = (
                "correlation_matrix"
            )

            data_uses_full_population = (
                True
            )

            data_is_sampled = (
                False
            )

            data_sample_size = 0

            data_sampling_reason = ""

            data_scope_note = (
                "고객별 행동 지표는 전체 원천 데이터 기준으로 계산하고, "
                "DuckDB corr()로 전체 고객 기준 상관계수를 계산한 뒤 "
                "작은 상관계수 행렬만 시각화 단계에 전달합니다."
            )


        elif len(relation_features) == 2:

            data_result_type = (
                "visual_sample"
            )

            data_uses_full_population = (
                False
            )

            data_is_sampled = (
                True
            )

            data_sample_size = (
                MAX_VISUAL_SAMPLE_ROWS
            )

            data_sampling_reason = (
                "두 행동 지표의 관계를 점 단위로 표현하기 위해 "
                "전체 원천 데이터에서 고객별 지표를 정확히 계산한 뒤 "
                "최종 고객 행만 시각화용으로 표본 추출합니다."
            )

            data_scope_note = (
                f"전체 원천 데이터 기준으로 고객별 지표를 계산한 뒤 "
                f"관계 시각화를 위해 최대 "
                f"{MAX_VISUAL_SAMPLE_ROWS:,}명의 고객 행을 사용합니다."
            )


        # -------------------------------------------------
        # 메타데이터와 실제 SQL 강제 일치
        # -------------------------------------------------
        #
        # 위 관계 요청 강제 보정에 해당하지 않는 일반 요청은
        # Data Agent의 result_type을 사용합니다.
        # -------------------------------------------------

        if data_result_type == "visual_sample":

            requested_sample_size = (
                data_sample_size
                if data_sample_size > 0
                else MAX_VISUAL_SAMPLE_ROWS
            )

            data_sample_size = min(
                requested_sample_size,
                MAX_VISUAL_SAMPLE_ROWS,
            )

            sql_query = ensure_visual_sample_sql(
                sql_query=sql_query,
                sample_size=data_sample_size,
            )

            data_uses_full_population = False
            data_is_sampled = True

            if not data_sampling_reason:
                data_sampling_reason = (
                    "전체 원천 데이터에서 분석 단위를 정확히 계산한 뒤 "
                    "행 단위 시각화를 위해 최종 결과만 표본 추출했습니다."
                )

            if not data_scope_note:
                data_scope_note = (
                    f"전체 원천 데이터 기준으로 지표를 계산한 뒤 "
                    f"시각화용 최종 행을 최대 {data_sample_size:,}개 사용합니다."
                )


        elif data_result_type == "correlation_matrix":

            data_uses_full_population = True
            data_is_sampled = False
            data_sample_size = 0

            if not data_scope_note:
                data_scope_note = (
                    "상관계수는 DuckDB에서 전체 데이터 기준으로 계산하고 "
                    "작은 상관계수 행렬만 후속 단계에 전달합니다."
                )


        else:

            # full_result는 전체 결과를 의미하도록 메타데이터 정규화
            data_result_type = "full_result"
            data_uses_full_population = True
            data_is_sampled = False
            data_sample_size = 0


        print(
            "[Data 범위 정책]"
        )

        print(
            "result_type =",
            data_result_type,
        )

        print(
            "uses_full_population =",
            data_uses_full_population,
        )

        print(
            "is_sampled =",
            data_is_sampled,
        )


        print(
            "[생성된 SQL]"
        )


        print(
            sql_query
        )


        is_valid, message = (
            validate_select_sql(
                sql_query
            )
        )


        if not is_valid:

            return Command(

                update={
                    "sql_query":
                        sql_query,

                    "data_purpose":
                        purpose,

                    "data_result_type":
                        data_result_type,

                    "data_uses_full_population":
                        data_uses_full_population,

                    "data_is_sampled":
                        data_is_sampled,

                    "data_sample_size":
                        data_sample_size,

                    "data_sampling_reason":
                        data_sampling_reason,

                    "data_scope_note":
                        data_scope_note,

                    "data_error":
                        message,

                    "error":
                        message,
                },

                goto=
                    "final_response",
            )


    except Exception as e:

        error = repr(
            e
        )


        print(
            f"[Data Agent 오류] "
            f"{error}"
        )


        return Command(

            update={
                "data_error":
                    error,

                "error":
                    error,
            },

            goto=
                "final_response",
        )


    return Command(

        update={
            "current_agent":
                "data_agent",

            "sql_query":
                sql_query,

            "data_purpose":
                purpose,

            "data_result_type":
                data_result_type,

            "data_uses_full_population":
                data_uses_full_population,

            "data_is_sampled":
                data_is_sampled,

            "data_sample_size":
                data_sample_size,

            "data_sampling_reason":
                data_sampling_reason,

            "data_scope_note":
                data_scope_note,

            "data_error":
                "",
        },

        goto=
            "sql_executor",
    )


# =========================================================
# SQL Executor
# =========================================================

def sql_executor_node(
    state: AgentState
) -> Command[
    Literal[
        "supervisor",
        "final_response",
    ]
]:

    print(
        "[SQL Executor]"
    )


    sql_query = state.get(
        "sql_query",
        ""
    )


    df, error = (
        execute_sql_to_dataframe(
            sql_query
        )
    )


    if df is None:

        return Command(

            update={
                "dataframe":
                    None,

                "data_row_count":
                    0,

                "data_columns":
                    [],

                "data_error":
                    error,

                "error":
                    error,
            },

            goto=
                "final_response",
        )


    row_count = len(
        df
    )


    columns = list(
        df.columns
    )


    print(
        f"[조회 완료] "
        f"{row_count:,} rows"
    )


    print(
        f"[컬럼] "
        f"{columns}"
    )


    completed_steps = (
        add_completed_step(
            state,
            "data"
        )
    )


    return Command(

        update={
            "dataframe":
                df,

            "data_row_count":
                row_count,

            "data_columns":
                columns,

            "data_error":
                "",

            "error":
                "",

            "completed_steps":
                completed_steps,
        },

        goto=
            "supervisor",
    )


# =========================================================
# Prep Agent Node
# =========================================================

def prep_node(
    state: AgentState
) -> Command[
    Literal[
        "supervisor",
        "final_response",
    ]
]:

    print(
        "[Prep Agent]"
    )


    if prep_agent is None:

        return Command(

            update={
                "prep_error":
                    "Prep Agent 연결 실패",

                "error":
                    "Prep Agent 연결 실패",
            },

            goto=
                "final_response",
        )


    df = get_analysis_dataframe(
        state
    )


    if not isinstance(
        df,
        pd.DataFrame
    ):

        return Command(

            update={
                "prep_error":
                    "전처리할 DataFrame이 없습니다.",

                "error":
                    "전처리할 DataFrame이 없습니다.",
            },

            goto=
                "final_response",
        )


    info = (
        check_dataframe_info(
            df
        )
    )


    missing_df = (
        check_missing_values(
            df
        )
    )


    duplicate_info = (
        check_duplicates(
            df
        )
    )


    infinite_df = (
        check_infinite_values(
            df
        )
    )


    prep_context = f"""
사용자 요청:

{state["question"]}


현재 DataFrame 정보:

행 수:
{info["row_count"]}

컬럼 수:
{info["column_count"]}

컬럼:
{info["columns"]}

dtype:
{info["dtypes"]}


========================================
사전 진단 정보
========================================

아래 결과는 작업 계획을 세우기 위한 미리보기입니다.
아직 사용자가 요청한 Prep 작업이 완료된 것으로
간주하지 마십시오.


결측치 사전 진단:

{missing_df.to_string(index=False)}


중복 사전 진단:

{duplicate_info}


무한대 값 사전 진단:

{infinite_df.to_string(index=False)}


사용자가 명시적으로 요청한 검사는
결과가 0건이어도 actions에 포함하십시오.
"""


    try:

        result = prep_agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=
                            prep_context
                    )
                ]
            },

            config={
                "recursion_limit": 20
            }
        )


        plan = result.get(
            "structured_response"
        )


        if plan is None:

            raise ValueError(
                "Prep Agent가 전처리 계획을 "
                "반환하지 않았습니다."
            )


    except Exception as e:

        error = repr(
            e
        )


        print(
            f"[Prep Agent 오류] "
            f"{error}"
        )


        return Command(

            update={
                "prep_error":
                    error,

                "error":
                    error,
            },

            goto=
                "final_response",
        )


    actions = (
        enforce_requested_prep_actions(
            question=
                state["question"],

            actions=
                list(
                    plan.actions
                ),
        )
    )


    print(
        "[Prep Plan]"
    )


    print(
        f"actions = "
        f"{actions}"
    )


    print(
        f"reason = "
        f"{plan.reason}"
    )


    processed_df = (
        df.copy()
    )


    action_reports = {}


    if (
        "check_dataframe_info"
        in actions
    ):

        action_reports[
            "check_dataframe_info"
        ] = (
            check_dataframe_info(
                processed_df
            )
        )


    if (
        "check_missing_values"
        in actions
    ):

        report_df = (
            check_missing_values(
                processed_df
            )
        )


        action_reports[
            "check_missing_values"
        ] = (
            report_df.to_dict(
                orient="records"
            )
        )


    if (
        "check_duplicates"
        in actions
    ):

        action_reports[
            "check_duplicates"
        ] = (
            check_duplicates(
                processed_df
            )
        )


    if (
        "convert_timestamp"
        in actions
    ):

        processed_df, report = (
            convert_timestamp(
                processed_df
            )
        )


        action_reports[
            "convert_timestamp"
        ] = report


    if (
        "sort_by_timestamp"
        in actions
    ):

        processed_df, report = (
            sort_by_timestamp(
                processed_df
            )
        )


        action_reports[
            "sort_by_timestamp"
        ] = report


    if (
        "create_repeat_count_feature"
        in actions
    ):

        group_columns = list(
            getattr(
                plan,
                "repeat_group_columns",
                [],
            )
        )


        if not group_columns:

            if all(
                column
                in processed_df.columns

                for column in [
                    "client_id",
                    "timestamp",
                    "sku",
                ]
            ):

                group_columns = [
                    "client_id",
                    "timestamp",
                    "sku",
                ]


            elif all(
                column
                in processed_df.columns

                for column in [
                    "client_id",
                    "timestamp",
                    "url",
                ]
            ):

                group_columns = [
                    "client_id",
                    "timestamp",
                    "url",
                ]


            elif all(
                column
                in processed_df.columns

                for column in [
                    "client_id",
                    "timestamp",
                    "query",
                ]
            ):

                group_columns = [
                    "client_id",
                    "timestamp",
                    "query",
                ]


            else:

                group_columns = [
                    column

                    for column in [
                        "client_id",
                        "timestamp",
                    ]

                    if column
                    in processed_df.columns
                ]


        processed_df, report = (
            create_repeat_count_feature(

                processed_df,

                group_columns=
                    group_columns,
            )
        )


        action_reports[
            "create_repeat_count_feature"
        ] = report


    if (
        "check_infinite_values"
        in actions
    ):

        report_df = (
            check_infinite_values(
                processed_df
            )
        )


        action_reports[
            "check_infinite_values"
        ] = (
            report_df.to_dict(
                orient="records"
            )
        )


    if (
        "replace_infinite_with_nan"
        in actions
    ):

        processed_df, report = (
            replace_infinite_with_nan(
                processed_df
            )
        )


        action_reports[
            "replace_infinite_with_nan"
        ] = report


    if (
        "handle_feature_missing_values"
        in actions
    ):

        processed_df, report = (
            handle_feature_missing_values(

                processed_df,

                zero_fill_columns=
                    getattr(
                        plan,
                        "zero_fill_columns",
                        [],
                    ),

                median_fill_columns=
                    getattr(
                        plan,
                        "median_fill_columns",
                        [],
                    ),

                category_fill_columns=
                    getattr(
                        plan,
                        "category_fill_columns",
                        [],
                    ),
            )
        )


        action_reports[
            "handle_feature_missing_values"
        ] = report


    after_info = (
        check_dataframe_info(
            processed_df
        )
    )


    after_missing_df = (
        check_missing_values(
            processed_df
        )
    )


    after_duplicate_info = (
        check_duplicates(
            processed_df
        )
    )


    after_infinite_df = (
        check_infinite_values(
            processed_df
        )
    )


    data_quality_report = {

        "plan_reason":
            plan.reason,

        "actions":
            actions,

        "action_reports":
            action_reports,

        "before": {

            "info":
                info,

            "missing":
                missing_df.to_dict(
                    orient="records"
                ),

            "duplicates":
                duplicate_info,

            "infinite":
                infinite_df.to_dict(
                    orient="records"
                ),
        },

        "after": {

            "info":
                after_info,

            "missing":
                after_missing_df.to_dict(
                    orient="records"
                ),

            "duplicates":
                after_duplicate_info,

            "infinite":
                after_infinite_df.to_dict(
                    orient="records"
                ),
        },
    }


    completed_steps = (
        add_completed_step(
            state,
            "prep"
        )
    )


    print(
        "[Prep 완료]"
    )


    print(
        f"shape = "
        f"{processed_df.shape}"
    )


    print(
        f"completed_steps = "
        f"{completed_steps}"
    )


    return Command(

        update={
            "processed_dataframe":
                processed_df,

            "prep_actions":
                actions,

            "data_quality_report":
                data_quality_report,

            "prep_error":
                "",

            "error":
                "",

            "completed_steps":
                completed_steps,

            "current_agent":
                "prep_agent",
        },

        goto=
            "supervisor",
    )


# =========================================================
# Visualization Agent Node
# =========================================================

def visualization_node(
    state: AgentState
) -> Command[
    Literal[
        "supervisor",
        "final_response",
    ]
]:

    print(
        "[Visualization Agent]"
    )


    if visualization_agent is None:

        return Command(

            update={
                "visualization_error":
                    "Visualization Agent 연결 실패",

                "error":
                    "Visualization Agent 연결 실패",
            },

            goto=
                "final_response",
        )


    df = get_analysis_dataframe(
        state
    )


    if not isinstance(
        df,
        pd.DataFrame
    ):

        return Command(

            update={
                "visualization_error":
                    "시각화할 DataFrame이 없습니다.",

                "error":
                    "시각화할 DataFrame이 없습니다.",
            },

            goto=
                "final_response",
        )


    # =====================================================
    # Agent 판단용 Context
    # =====================================================

    numeric_columns = list(
        df.select_dtypes(
            include="number"
        ).columns
    )


    # 숫자로 저장되어 있어도 분석용 연속형 Feature가 아닌
    # 식별자 / 범주 ID 컬럼은 별도로 관리합니다.
    identifier_columns = {
        "client_id",
        "sku",
        "category",
        "url",
    }


    numeric_feature_columns = [
        column
        for column in numeric_columns
        if column not in identifier_columns
    ]


    datetime_columns = [
        column
        for column in df.columns
        if pd.api.types.is_datetime64_any_dtype(
            df[column]
        )
    ]


    dtype_info = {
        column: str(dtype)
        for column, dtype in df.dtypes.items()
    }


    # =====================================================
    # 컬럼별 고유값 참고 정보
    # =====================================================
    #
    # 수천만 행 DataFrame에서 모든 컬럼의 nunique()를
    # 전체 데이터에 실행하면 응답이 다시 느려질 수 있습니다.
    #
    # 따라서
    # - 200,000행 이하: 전체 데이터 기준
    # - 200,000행 초과: 최대 100,000행의 간격 표본 기준
    #
    # 으로 cardinality 참고 정보를 만듭니다.
    # 표본 기준 값은 정확한 전체 고유값 수가 아니라
    # 차트 선택을 위한 참고 정보입니다.
    # =====================================================

    row_count = len(
        df
    )


    exact_unique_limit = 200_000
    unique_sample_limit = 100_000


    if row_count <= exact_unique_limit:

        unique_source_df = df
        unique_count_mode = "전체 데이터 기준"

    else:

        step = max(
            row_count
            // unique_sample_limit,
            1,
        )


        unique_source_df = (
            df
            .iloc[::step]
            .head(
                unique_sample_limit
            )
        )


        unique_count_mode = (
            f"표본 {len(unique_source_df):,}행 기준"
        )


    unique_counts = {}


    for column in df.columns:

        try:

            unique_counts[column] = int(
                unique_source_df[column]
                .nunique(
                    dropna=False
                )
            )

        except Exception:

            # list 등 hash 불가능한 값이 들어 있는 컬럼은
            # 차트 선택용 고유값 계산에서 제외합니다.
            unique_counts[column] = (
                "계산 불가"
            )


    sample_text = (
        df
        .head(10)
        .to_string(
            index=False
        )
    )


    visualization_context = f"""
사용자 요청:

{state["question"]}


데이터 범위 메타데이터:

result_type:
{state.get("data_result_type", "full_result")}

uses_full_population:
{state.get("data_uses_full_population", True)}

is_sampled:
{state.get("data_is_sampled", False)}

sample_size:
{state.get("data_sample_size", 0)}

sampling_reason:
{state.get("data_sampling_reason", "")}

scope_note:
{state.get("data_scope_note", "")}


현재 시각화 대상 DataFrame 정보:

행 수:
{len(df)}

컬럼:
{list(df.columns)}

dtype:
{dtype_info}

숫자로 저장된 전체 컬럼:
{numeric_columns}

분석 가능한 숫자형 Feature 후보:
{numeric_feature_columns}

식별자 / 범주 ID 컬럼:
{[column for column in df.columns if column in identifier_columns]}

datetime 컬럼:
{datetime_columns}

컬럼별 고유값 참고 정보:
{unique_counts}

고유값 계산 기준:
{unique_count_mode}


샘플 데이터:

{sample_text}


현재 DataFrame에 실제 존재하는 컬럼만 사용하고,
사용자 질문과 데이터 특징을 함께 고려하여
가장 적절한 차트 하나를 계획하십시오.
"""


    try:

        result = visualization_agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=
                            visualization_context
                    )
                ]
            },

            config={
                "recursion_limit": 20
            }
        )


        plan = result.get(
            "structured_response"
        )


        if plan is None:

            raise ValueError(
                "Visualization Agent가 "
                "차트 계획을 반환하지 않았습니다."
            )


    except Exception as e:

        error = repr(
            e
        )


        print(
            f"[Visualization Agent 오류] "
            f"{error}"
        )


        return Command(

            update={
                "visualization_error":
                    error,

                "error":
                    error,
            },

            goto=
                "final_response",
        )


    # =====================================================
    # 사용자 표현 + DataFrame 구조 기반 차트 규칙 보정
    # =====================================================
    #
    # LLM 계획을 기본으로 사용하되,
    # 사용자가 차트를 직접 지정했거나
    # 관계/상관관계처럼 변수 개수가 핵심인 경우에는
    # 최소 규칙으로 한 번 더 보정합니다.
    #
    # 예:
    # - 숫자 Feature 2개 관계 -> scatter
    # - 숫자 Feature 3개 이상 전체 관계 -> heatmap
    # =====================================================

    question_lower = (
        state["question"]
        .lower()
    )


    data_result_type = state.get(
        "data_result_type",
        "full_result",
    )

    precomputed_correlation = (
        data_result_type
        == "correlation_matrix"
    )


    explicit_heatmap = any(
        keyword in question_lower
        for keyword in [
            "히트맵",
            "heatmap",
        ]
    )


    explicit_scatter = any(
        keyword in question_lower
        for keyword in [
            "산점도",
            "scatter",
        ]
    )


    relationship_request = any(
        keyword in question_lower
        for keyword in [
            "상관관계",
            "상관 관계",
            "상관계수",
            "상관 계수",
            "correlation",
            "correlation matrix",
            "관계를 보여",
            "관계 보여",
            "관계를 알려",
            "관계 알려",
            "관계 분석",
            "관련성을 보여",
            "관련성 알려",
            "연관성을 보여",
            "연관성 알려",
        ]
    )


    if precomputed_correlation:

        plan.chart_type = (
            "heatmap"
        )

        plan.heatmap_columns = [
            column
            for column in numeric_feature_columns
            if column != "feature"
        ]

        plan.x_column = None
        plan.y_column = None


    elif explicit_heatmap:

        plan.chart_type = (
            "heatmap"
        )


        if not plan.heatmap_columns:

            plan.heatmap_columns = (
                numeric_feature_columns
            )


    elif explicit_scatter:

        plan.chart_type = (
            "scatter"
        )


        if (
            len(numeric_feature_columns)
            >= 2
        ):

            if not plan.x_column:
                plan.x_column = (
                    numeric_feature_columns[0]
                )


            if not plan.y_column:
                plan.y_column = (
                    numeric_feature_columns[1]
                )


    elif relationship_request:

        if (
            len(numeric_feature_columns)
            >= 3
        ):

            plan.chart_type = (
                "heatmap"
            )


            plan.heatmap_columns = (
                numeric_feature_columns
            )


            plan.x_column = None
            plan.y_column = None


        elif (
            len(numeric_feature_columns)
            == 2
        ):

            plan.chart_type = (
                "scatter"
            )


            plan.x_column = (
                numeric_feature_columns[0]
            )


            plan.y_column = (
                numeric_feature_columns[1]
            )


    # title / reason이 비어 있어도
    # Visualization Executor가 정상 실행되도록 fallback을 사용합니다.
    chart_title = (
        plan.title.strip()
        if isinstance(plan.title, str)
        and plan.title.strip()
        else state["question"].strip()
    )


    plan_reason = (
        plan.reason.strip()
        if isinstance(plan.reason, str)
        and plan.reason.strip()
        else (
            "사용자 요청과 DataFrame 구조를 기준으로 "
            "가장 적절한 차트를 선택했습니다."
        )
    )


    print(
        "[Visualization Plan]"
    )


    print(
        f"chart_type = "
        f"{plan.chart_type}"
    )


    print(
        f"reason = "
        f"{plan_reason}"
    )


    # =====================================================
    # Visualization Executor
    # =====================================================

    try:

        if (
            plan.chart_type
            == "bar"
        ):

            chart_result = (
                create_bar_chart(
                    df=df,
                    x_column=plan.x_column,
                    y_column=plan.y_column,
                    title=chart_title,
                    x_label=plan.x_label,
                    y_label=plan.y_label,
                    top_n=plan.top_n,
                    sort_descending=
                        plan.sort_descending,
                )
            )


        elif (
            plan.chart_type
            == "line"
        ):

            chart_result = (
                create_line_chart(
                    df=df,
                    x_column=plan.x_column,
                    y_column=plan.y_column,
                    title=chart_title,
                    x_label=plan.x_label,
                    y_label=plan.y_label,
                )
            )


        elif (
            plan.chart_type
            == "histogram"
        ):

            chart_result = (
                create_histogram(
                    df=df,
                    column=plan.value_column,
                    title=chart_title,
                    x_label=plan.x_label,
                    y_label=plan.y_label,
                    bins=plan.bins,
                )
            )


        elif (
            plan.chart_type
            == "boxplot"
        ):

            chart_result = (
                create_boxplot(
                    df=df,
                    value_column=
                        plan.value_column,
                    category_column=
                        plan.category_column,
                    title=
                        chart_title,
                    x_label=
                        plan.x_label,
                    y_label=
                        plan.y_label,
                    top_n_categories=
                        plan.top_n,
                )
            )


        elif (
            plan.chart_type
            == "scatter"
        ):

            chart_result = (
                create_scatterplot(
                    df=df,
                    x_column=
                        plan.x_column,
                    y_column=
                        plan.y_column,
                    title=
                        chart_title,
                    x_label=
                        plan.x_label,
                    y_label=
                        plan.y_label,
                )
            )


        elif (
            plan.chart_type
            == "heatmap"
        ):

            heatmap_columns = (
                plan.heatmap_columns
                or None
            )


            chart_result = (
                create_correlation_heatmap(
                    df=df,
                    title=
                        chart_title,
                    columns=
                        heatmap_columns,
                    precomputed=
                        precomputed_correlation,
                    row_label_column=
                        "feature",
                )
            )


        else:

            raise ValueError(
                f"지원하지 않는 차트 유형: "
                f"{plan.chart_type}"
            )


    except Exception as e:

        error = repr(
            e
        )


        print(
            f"[Visualization Executor 오류] "
            f"{error}"
        )


        return Command(

            update={
                "visualization_error":
                    error,

                "error":
                    error,
            },

            goto=
                "final_response",
        )


    if not chart_result.get(
        "success"
    ):

        error = (
            chart_result.get(
                "error"
            )
            or "차트 생성 실패"
        )


        return Command(

            update={
                "visualization_result":
                    chart_result,

                "visualization_path":
                    None,

                "visualization_error":
                    error,

                "error":
                    error,
            },

            goto=
                "final_response",
        )


    visualization_result = {
        **chart_result,

        "plan_reason":
            plan_reason,

        "data_result_type":
            data_result_type,

        "data_uses_full_population":
            state.get(
                "data_uses_full_population",
                True,
            ),

        "data_is_sampled":
            state.get(
                "data_is_sampled",
                False,
            ),

        "data_sample_size":
            state.get(
                "data_sample_size",
                0,
            ),

        "data_sampling_reason":
            state.get(
                "data_sampling_reason",
                "",
            ),

        "data_scope_note":
            state.get(
                "data_scope_note",
                "",
            ),
    }


    completed_steps = (
        add_completed_step(
            state,
            "visualization"
        )
    )


    print(
        "[Visualization 완료]"
    )


    print(
        f"file_path = "
        f"{chart_result.get('file_path')}"
    )


    print(
        f"completed_steps = "
        f"{completed_steps}"
    )


    return Command(

        update={
            "visualization_result":
                visualization_result,

            "visualization_path":
                chart_result.get(
                    "file_path"
                ),

            "visualization_error":
                "",

            "error":
                "",

            "completed_steps":
                completed_steps,

            "current_agent":
                "visualization_agent",
        },

        goto=
            "supervisor",
    )


# =========================================================
# Model Agent Node
# 아직 미구현
# =========================================================

def model_node(
    state: AgentState
) -> Command[
    Literal[
        "supervisor",
        "final_response",
    ]
]:

    print(
        "[Model Agent]"
    )


    return Command(

        update={
            "model_error":
                "Model Agent는 아직 구현되지 않았습니다."
        },

        goto=
            "final_response",
    )


# =========================================================
# Experiment Agent Node
# 아직 미구현
# =========================================================

def experiment_node(
    state: AgentState
) -> Command[
    Literal[
        "supervisor",
        "final_response",
    ]
]:

    print(
        "[Experiment Agent]"
    )


    return Command(

        update={
            "experiment_error":
                "Experiment Agent는 아직 구현되지 않았습니다."
        },

        goto=
            "final_response",
    )


# =========================================================
# Final Response Node
# =========================================================

def final_response_node(
    state: AgentState
) -> Command[
    Literal[
        "__end__"
    ]
]:

    print(
        "[Final Response]"
    )


    error = state.get(
        "error",
        ""
    )


    if error:

        final_answer = (
            "작업을 완료하지 못했습니다.\n\n"
            f"오류: {error}"
        )


    else:

        completed_steps = (
            state.get(
                "completed_steps",
                []
            )
        )


        # =================================================
        # Visualization 결과가 있는 경우
        # =================================================

        if (
            "visualization"
            in completed_steps
        ):

            visualization_result = (
                state.get(
                    "visualization_result",
                    {}
                )
                or {}
            )


            final_answer = (
                "시각화가 완료되었습니다.\n\n"
                f"[차트]\n"
                f"- 종류: "
                f"{visualization_result.get('chart_type', '')}\n"
                f"- 제목: "
                f"{visualization_result.get('title', '')}\n"
                f"- 선택 이유: "
                f"{visualization_result.get('plan_reason', '')}\n"
                f"- 저장 위치: "
                f"{state.get('visualization_path', '')}\n"
            )




            # 히스토그램 Log Scale 적용 여부
            if (
                visualization_result.get(
                    "chart_type"
                )
                == "histogram"
            ):

                log_applied = (
                    visualization_result.get(
                        "log_scale_applied",
                        False
                    )
                )


                if log_applied:

                    final_answer += (
                        "- 분포 특성: "
                        "긴 꼬리 분포를 고려해 "
                        "빈도 축에 log scale을 적용했습니다.\n"
                    )

            data_scope_note = (
                visualization_result.get(
                    "data_scope_note",
                    "",
                )
                or ""
            )

            data_is_sampled = bool(
                visualization_result.get(
                    "data_is_sampled",
                    False,
                )
            )

            tool_sampled = bool(
                visualization_result.get(
                    "sampled",
                    False,
                )
            )

            if (
                visualization_result.get(
                    "data_result_type"
                )
                == "correlation_matrix"
            ):
                final_answer += (
                    "\n[데이터 범위]\n"
                    "- 상관계수는 DuckDB에서 전체 데이터 기준으로 계산했습니다.\n"
                    "- pandas에는 작은 상관계수 행렬만 전달했습니다.\n"
                )

            elif data_is_sampled:
                final_answer += (
                    "\n[데이터 범위]\n"
                    "- 정확한 원천 집계는 DuckDB에서 수행하고, "
                    "행 단위 시각화를 위해 표본을 사용했습니다.\n"
                )

                if data_scope_note:
                    final_answer += (
                        f"- {data_scope_note}\n"
                    )

            elif data_scope_note:
                final_answer += (
                    "\n[데이터 범위]\n"
                    f"- {data_scope_note}\n"
                )

            if tool_sampled:
                sampling_note = (
                    visualization_result.get(
                        "sampling_note",
                        "",
                    )
                    or ""
                )

                if sampling_note:
                    final_answer += (
                        f"- {sampling_note}\n"
                    )


            if (
                "prep"
                in completed_steps
            ):

                prep_actions = (
                    state.get(
                        "prep_actions",
                        []
                    )
                )


                final_answer += (
                    "\n[선행 전처리]\n"
                    + (
                        "\n".join(
                            f"- {action}"
                            for action
                            in prep_actions
                        )
                        if prep_actions
                        else "- 추가 전처리 없음"
                    )
                )


        # =================================================
        # Prep 결과
        # =================================================

        elif (
            "prep"
            in completed_steps
        ):

            report = (
                state.get(
                    "data_quality_report",
                    {}
                )
            )


            processed_df = (
                state.get(
                    "processed_dataframe"
                )
            )


            actions = (
                report.get(
                    "actions",
                    []
                )
            )


            action_reports = (
                report.get(
                    "action_reports",
                    {}
                )
            )


            final_answer = (
                "데이터 품질 확인 및 전처리가 "
                "완료되었습니다.\n\n"
            )


            if actions:

                final_answer += (
                    "[수행 작업]\n"
                    + "\n".join(
                        f"- {action}"
                        for action in actions
                    )
                    + "\n\n"
                )


            missing_report = (
                action_reports.get(
                    "check_missing_values"
                )
            )


            if missing_report is not None:

                missing_columns = [
                    item

                    for item in missing_report

                    if item.get(
                        "null_count",
                        0
                    ) > 0
                ]


                total_missing = sum(
                    item.get(
                        "null_count",
                        0
                    )

                    for item
                    in missing_report
                )


                final_answer += (
                    "[결측치 검사]\n"
                    f"- 전체 결측치: "
                    f"{total_missing:,}개\n"
                    f"- 결측치가 있는 컬럼: "
                    f"{len(missing_columns)}개\n\n"
                )


            duplicate_report = (
                action_reports.get(
                    "check_duplicates"
                )
            )


            if duplicate_report is not None:

                final_answer += (
                    "[중복 검사]\n"
                    f"- 전체 행: "
                    f"{duplicate_report.get('row_count', 0):,}개\n"
                    f"- 중복 행: "
                    f"{duplicate_report.get('duplicate_rows', 0):,}개\n"
                    f"- 중복 비율: "
                    f"{duplicate_report.get('duplicate_ratio', 0):.2%}\n\n"
                )


            if isinstance(
                processed_df,
                pd.DataFrame
            ):

                final_answer += (
                    "[처리된 DataFrame]\n"
                    f"- 행: "
                    f"{len(processed_df):,}\n"
                    f"- 열: "
                    f"{len(processed_df.columns):,}\n"
                    f"- 컬럼: "
                    f"{list(processed_df.columns)}\n"
                )


                preview = (
                    processed_df
                    .head(10)
                    .to_string(
                        index=False
                    )
                )


                final_answer += (
                    "\n[상위 10개 행]\n"
                    f"{preview}"
                )


        # =================================================
        # Data 결과만 있는 경우
        # =================================================

        else:

            df = state.get(
                "dataframe"
            )


            if isinstance(
                df,
                pd.DataFrame
            ):

                row_count = len(
                    df
                )


                display_rows = min(
                    row_count,
                    100
                )


                preview = (
                    df
                    .head(
                        display_rows
                    )
                    .to_string(
                        index=False
                    )
                )


                final_answer = (
                    f"{state.get('data_purpose', '데이터 조회')}\n\n"

                    f"조회 결과: "
                    f"{row_count:,}행\n\n"

                    f"{preview}"
                )


                if (
                    row_count
                    > display_rows
                ):

                    final_answer += (
                        f"\n\n전체 {row_count:,}행 중 "
                        f"상위 {display_rows}행을 표시했습니다."
                    )


            else:

                final_answer = (
                    "현재까지 생성된 데이터 결과가 없습니다."
                )


        # =================================================
        # 미구현 Agent 안내
        # =================================================

        reason = state.get(
            "supervisor_reason",
            ""
        )


        if (
            "아직 해당 Agent가 구현되지 않았으므로"
            in reason
        ):

            final_answer += (
                "\n\n[현재 구현 상태]\n"
                f"{reason}"
            )


    final_answer = append_verification_sql(
        final_answer=final_answer,
        state=state,
    )


    message = AIMessage(

        content=
            final_answer,

        name=
            "final_response",
    )


    return Command(

        update={
            "messages": [
                message
            ],

            "final_answer":
                final_answer,

            "current_agent":
                "final_response",
        },

        goto=
            END,
    )
