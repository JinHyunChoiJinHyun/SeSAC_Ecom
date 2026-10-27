import pandas as pd

from typing import Literal

from langgraph.graph import END
from langgraph.types import Command

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
)

from state import AgentState

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
    step: str,
):
    """
    completed_steps에 동일한 작업이
    중복 추가되지 않도록 합니다.
    """

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
# 분석용 DataFrame 선택
# =========================================================

def get_analysis_dataframe(
    state: AgentState,
):
    """
    Prep Agent가 처리한 DataFrame이 있으면
    processed_dataframe을 우선 사용합니다.

    없으면 Data Agent가 조회한
    원본 dataframe을 사용합니다.
    """

    processed_df = state.get(
        "processed_dataframe"
    )

    if isinstance(
        processed_df,
        pd.DataFrame,
    ):

        return processed_df

    return state.get(
        "dataframe"
    )


# =========================================================
# 사용자 요청 Prep 작업 보장
# =========================================================

def enforce_requested_prep_actions(
    question: str,
    actions: list[str],
):
    """
    LLM이 사용자가 명시적으로 요청한
    품질 검사를 빠뜨리는 것을 방지합니다.
    """

    result = list(
        actions
    )

    question_lower = (
        question.lower()
    )


    # -----------------------------------------------------
    # 결측치
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 중복
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 무한대
    # -----------------------------------------------------

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


    # 중복 Action 제거
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

    Data → Final뿐 아니라

    Data → Prep → Final
    Data → Visualization → Final
    Data → Model → Final
    Data → Experiment → Final

    과 같은 흐름에서도 동일하게 적용됩니다.

    이후 Delivery Agent가 final_answer를
    Slack이나 Email로 전달하면
    SQL도 함께 전달할 수 있습니다.
    """

    sql_query = (
        state.get(
            "sql_query"
        )
    )


    # Data Agent가 사용되지 않은 요청이면
    # SQL을 추가하지 않습니다.
    if not sql_query:

        return final_answer


    sql_query = (
        str(
            sql_query
        )
        .strip()
    )


    if not sql_query:

        return final_answer


    data_purpose = (
        state.get(
            "data_purpose"
        )
    )


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
# Supervisor Node
# =========================================================

def supervisor_node(
    state: AgentState,
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
                pd.DataFrame,
            ),

        has_processed_dataframe=
            isinstance(
                state.get(
                    "processed_dataframe"
                ),
                pd.DataFrame,
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
    state: AgentState,
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
            },
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
    state: AgentState,
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
    state: AgentState,
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
        pd.DataFrame,
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


    # -----------------------------------------------------
    # 사전 진단
    # -----------------------------------------------------

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

아래 결과는 작업 계획을 위한
사전 진단 정보입니다.

아직 사용자가 요청한 Prep 작업이
완료된 것으로 간주하지 마십시오.


결측치:

{missing_df.to_string(index=False)}


중복:

{duplicate_info}


무한대:

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
            },
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
                state[
                    "question"
                ],

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


    # -----------------------------------------------------
    # DataFrame 정보
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 결측치
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 중복
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # Timestamp 변환
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # Timestamp 정렬
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 반복 이벤트 Feature
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 무한대 확인
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 무한대 → NaN
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # Feature 결측치 처리
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # 처리 후 품질 확인
    # -----------------------------------------------------

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
    state: AgentState,
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
        pd.DataFrame,
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
    # DataFrame 정보
    # =====================================================

    row_count = len(
        df
    )


    dtypes = {
        column: str(
            dtype
        )

        for column, dtype
        in df.dtypes.items()
    }


    numeric_columns = list(
        df.select_dtypes(
            include="number"
        ).columns
    )


    datetime_columns = [

        column

        for column
        in df.columns

        if pd.api.types.is_datetime64_any_dtype(
            df[
                column
            ]
        )
    ]


    # =====================================================
    # 고유값 수
    # =====================================================

    unique_check_limit = (
        200_000
    )


    if row_count <= unique_check_limit:

        unique_source = (
            df
        )

        unique_note = (
            "전체 DataFrame 기준"
        )

    else:

        unique_source = (
            df.head(
                unique_check_limit
            )
        )

        unique_note = (
            f"앞 {unique_check_limit:,}행 표본 기준"
        )


    unique_counts = {

        column:
            int(
                unique_source[
                    column
                ]
                .nunique(
                    dropna=True
                )
            )

        for column
        in df.columns
    }


    sample_text = (
        df
        .head(
            10
        )
        .to_string(
            index=False
        )
    )


    visualization_context = f"""
사용자 요청:

{state["question"]}


========================================
현재 DataFrame
========================================

전체 행 수:

{row_count}


컬럼:

{list(df.columns)}


dtype:

{dtypes}


숫자형 컬럼:

{numeric_columns}


datetime 컬럼:

{datetime_columns}


컬럼별 고유값 수:

{unique_counts}


고유값 계산 기준:

{unique_note}


샘플 데이터:

{sample_text}


========================================
요청
========================================

사용자의 요청과

현재 DataFrame의

- 행 수
- 컬럼
- dtype
- 고유값 수
- 데이터 의미

를 함께 고려하여

가장 적절한 차트 하나를 계획하십시오.


현재 DataFrame에 실제 존재하는
컬럼만 사용하십시오.
"""


    # =====================================================
    # Visualization Plan 생성
    # =====================================================

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
            },
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
    # Title 안전장치
    # =====================================================

    chart_title = (
        plan.title.strip()
        if plan.title
        else ""
    )


    if not chart_title:

        chart_title = (
            state.get(
                "question"
            )
            or "시각화 결과"
        )


    # =====================================================
    # Reason 안전장치
    # =====================================================

    plan_reason = (
        plan.reason.strip()
        if plan.reason
        else ""
    )


    if not plan_reason:

        plan_reason = (
            "사용자의 요청과 현재 DataFrame 구조를 "
            "기준으로 적절한 차트를 자동 선택했습니다."
        )


    print(
        "[Visualization Plan]"
    )


    print(
        f"chart_type = "
        f"{plan.chart_type}"
    )


    print(
        f"title = "
        f"{chart_title}"
    )


    print(
        f"reason = "
        f"{plan_reason}"
    )


    # =====================================================
    # Visualization Executor
    # =====================================================

    try:

        # -------------------------------------------------
        # Bar
        # -------------------------------------------------

        if (
            plan.chart_type
            == "bar"
        ):

            chart_result = (
                create_bar_chart(

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

                    top_n=
                        plan.top_n,

                    sort_descending=
                        plan.sort_descending,
                )
            )


        # -------------------------------------------------
        # Line
        # -------------------------------------------------

        elif (
            plan.chart_type
            == "line"
        ):

            chart_result = (
                create_line_chart(

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


        # -------------------------------------------------
        # Histogram
        # -------------------------------------------------

        elif (
            plan.chart_type
            == "histogram"
        ):

            chart_result = (
                create_histogram(

                    df=df,

                    column=
                        plan.value_column,

                    title=
                        chart_title,

                    x_label=
                        plan.x_label,

                    y_label=
                        plan.y_label,

                    bins=
                        plan.bins,
                )
            )


        # -------------------------------------------------
        # Boxplot
        # -------------------------------------------------

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


        # -------------------------------------------------
        # Scatter
        # -------------------------------------------------

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


        # -------------------------------------------------
        # Heatmap
        # -------------------------------------------------

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


    # =====================================================
    # Tool 자체 실패
    # =====================================================

    if not chart_result.get(
        "success"
    ):

        error = (
            chart_result.get(
                "error"
            )
            or "차트 생성 실패"
        )


        print(
            f"[Visualization 실패] "
            f"{error}"
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


    # =====================================================
    # Visualization 결과
    # =====================================================

    visualization_result = {

        **chart_result,

        "plan_reason":
            plan_reason,
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
# Model Agent Placeholder
# =========================================================

def model_node(
    state: AgentState,
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
# Experiment Agent Placeholder
# =========================================================

def experiment_node(
    state: AgentState,
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
    state: AgentState,
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


    # =====================================================
    # 오류
    # =====================================================

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
        # Visualization 결과
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

                "[차트]\n"

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


            # Prep도 같이 수행한 경우
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
                )


                if prep_actions:

                    final_answer += (
                        "\n".join(
                            f"- {action}"
                            for action
                            in prep_actions
                        )
                    )

                else:

                    final_answer += (
                        "- 추가 전처리 없음"
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
                or {}
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
                        for action
                        in actions
                    )
                    + "\n\n"
                )


            # ---------------------------------------------
            # 결측치 결과
            # ---------------------------------------------

            missing_report = (
                action_reports.get(
                    "check_missing_values"
                )
            )


            if missing_report is not None:

                missing_columns = [

                    item

                    for item
                    in missing_report

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


            # ---------------------------------------------
            # 중복 결과
            # ---------------------------------------------

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


            # ---------------------------------------------
            # 처리된 DataFrame
            # ---------------------------------------------

            if isinstance(
                processed_df,
                pd.DataFrame,
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
                    .head(
                        10
                    )
                    .to_string(
                        index=False
                    )
                )


                final_answer += (
                    "\n[상위 10개 행]\n"
                    f"{preview}"
                )


        # =================================================
        # Data 조회만 수행한 경우
        # =================================================

        else:

            df = state.get(
                "dataframe"
            )


            if isinstance(
                df,
                pd.DataFrame,
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
                    "현재까지 생성된 "
                    "데이터 결과가 없습니다."
                )


        # =================================================
        # 아직 구현하지 않은 Agent 안내
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


    # =====================================================
    # 기반 데이터 검증용 SQL 추가
    # =====================================================
    #
    # Data Agent를 사용했다면
    # 성공/실패 여부와 관계없이
    # 생성된 SQL을 최종 응답에 포함합니다.
    #
    # 이후 Slack Delivery Agent가
    # final_answer를 그대로 전달하면
    # 사용자에게 SQL까지 함께 전달됩니다.
    # =====================================================

    final_answer = (
        append_verification_sql(

            final_answer=
                final_answer,

            state=
                state,
        )
    )


    # =====================================================
    # 최종 메시지 생성
    # =====================================================

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