import pandas as pd
import re

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


from .analysis_state import (
    AnalysisState,
)


from common.data_sources import (
    get_available_sources,
    get_sources,
)


from .router import (
    route_request,
    UNSUPPORTED_PREDICTION_REASON,
)


from .data_workflow import (
    get_data_agent,
)


from .prep_workflow import (
    get_prep_agent,
)


from .visualization_workflow import (
    get_visualization_agent,
)


from agent_system.common_tools.execution_tools.data_tools import (
    validate_select_sql,
    execute_sql_to_dataframe,
    build_filter_value_hint,
    SOURCE_UNAVAILABLE_PREFIX,
)


# 0행 결과·SQL 실행 오류 시 Data Agent에 SQL 수정을 다시 맡기는 최대 횟수
MAX_DATA_RETRY = 1


from agent_system.common_tools.execution_tools.prep_tools import (
    check_dataframe_info,
    detect_timestamp_column,
    detect_entity_column,
    default_repeat_group_columns,
    check_missing_values,
    check_duplicates,
    convert_timestamp,
    sort_by_timestamp,
    create_repeat_count_feature,
    check_infinite_values,
    replace_infinite_with_nan,
    handle_feature_missing_values,
)


from agent_system.common_tools.execution_tools.visualization_tools import (
    is_semantic_identifier,
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
    state: AnalysisState,
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
    state: AnalysisState
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
# SQL이 사용한 데이터 출처 판단
# =========================================================

def detect_sql_sources(sql_query: str, declared_sources=None) -> list:
    """SQL이 실제로 사용한 출처를 등록부 순서대로 반환합니다.

    SQL에 schema 이름(예: hm.transactions)이 있으면 그것을 기준으로 하고,
    찾지 못하면 Data Agent가 표시한 data_sources를 사용합니다.
    """

    lowered_sql = str(sql_query or "").lower()
    found = [
        source
        for source in get_sources()
        if re.search(rf"\b{re.escape(source.key)}\.", lowered_sql)
    ]
    if found:
        return found

    declared = {str(key).strip().lower() for key in (declared_sources or [])}
    return [source for source in get_sources() if source.key in declared]


# =========================================================
# 최종 응답에 검증용 SQL 추가
# =========================================================

def append_verification_sql(
    final_answer: str,
    state: AnalysisState,
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

    # 사용자에게는 개발용 검증 헤더 대신 간결한 출처 정보를 표시합니다.
    final_answer += "\n\n---\n분석 기준\n"

    if data_purpose:
        final_answer += (
            "- 분석 내용: "
            f"{data_purpose}\n"
        )

    # 출처 이름·주의 문구는 common/data_sources.py 등록부에서 읽습니다.
    data_sources = detect_sql_sources(
        sql_query,
        state.get("data_sources") or [],
    )

    if data_sources:
        final_answer += (
            "- 데이터 출처:\n"
            + "\n".join(f"- {source.display}" for source in data_sources)
            + "\n"
        )

    for source in data_sources:
        for note in source.answer_notes:
            final_answer += f"- 참고: {note}\n"

    final_answer += (
        "\n분석에 사용한 SQL:\n"
        "```sql\n"
        f"{sql_query}\n"
        "```"
    )

    return final_answer



# 고객 구매확률 Top-N 정확 조회: 시각화 요청에도 표본 추출하지 않습니다.
# =========================================================
# 대용량 시각화용 SQL 보정
# =========================================================

MAX_VISUAL_SAMPLE_ROWS = 100_000


def is_small_limited_result(sql_query: str) -> bool:
    """SQL 마지막에 LIMIT n(n ≤ 시각화 표본 상한)이 있으면 True. 표본 추출이 필요 없는 결과입니다."""

    text = re.sub(r"--.*?$", " ", str(sql_query or ""), flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    if re.search(r"\bUSING\s+SAMPLE\b|\bTABLESAMPLE\b", text, flags=re.IGNORECASE):
        return False
    match = re.search(r"\bLIMIT\s+(\d+)\s*;?\s*$", text.strip(), flags=re.IGNORECASE)
    return bool(match) and int(match.group(1)) <= MAX_VISUAL_SAMPLE_ROWS


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


def has_relation_intent(
    question: str,
) -> bool:
    """사용자 질문이 관계/상관관계 요청인지 확인합니다."""

    question_lower = str(question or "").lower()
    return any(keyword in question_lower for keyword in RELATION_INTENT_KEYWORDS)


def clean_relation_features(
    feature_columns,
) -> list[str]:
    """Data Agent가 알려준 관계 분석 지표 alias를 정리합니다.

    - SQL alias로 쓸 수 있는 이름만 남깁니다.
    - ID·범주 코드 컬럼은 상관계수 대상에서 제외합니다.
    - 순서를 유지하며 중복을 제거합니다.
    """

    cleaned = []
    for column in feature_columns or []:
        name = str(column or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        if is_semantic_identifier(name):
            continue
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


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
        "WITH metrics AS (\n"
        "    SELECT *\n"
        "    FROM (\n"
        f"{clean_sql}\n"
        "    ) AS base_metrics\n"
        "),\n"
        "correlation_values AS (\n"
        "    SELECT\n"
        f"{corr_select}\n"
        "    FROM metrics\n"
        ")\n"
        f"{matrix_rows};"
    )


# =========================================================
# Step Router Node (분석 그래프 내부의 단계 선택기)
# Data → Prep → Visualization → Final 중 다음 단계를 고릅니다.
# 최상위 Orchestrator(agent_system/orchestrator)와 다른 역할입니다.
# =========================================================

def step_router_node(
    state: AnalysisState
) -> Command[
    Literal[
        "data_agent",
        "prep_agent",
        "visualization_agent",
        "final_response",
    ]
]:

    # Slack/터미널 외부에서 그래프를 직접 호출해도 요청 내 모델을 고정합니다.
    from env.model_settings import get_request_config

    state = dict(state)
    if not state.get("llm_config"):
        state["llm_config"] = get_request_config().public()

    print(
        "[Step Router] 분석 단계 선택"
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

        model_config=
            state.get("llm_config"),

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

        has_visualization_result=
            state.get(
                "visualization_result"
            ) is not None,
    )


    if decision is None:

        return Command(

            update={
                "llm_config":
                    state["llm_config"],

                "question":
                    question,

                "error":
                    "Router 실행 오류",
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
            "llm_config":
                state["llm_config"],

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
    state: AnalysisState
) -> Command[
    Literal[
        "sql_executor",
        "final_response",
    ]
]:

    print(
        "[Data Agent]"
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


        data_agent = get_data_agent(
            state.get("llm_config")
        )

        retry_feedback = str(
            state.get("data_retry_feedback", "") or ""
        ).strip()

        from agent_system.common_tools.sql_semantic_rules import guidance
        agent_input = guidance(question)

        # 멀티턴 후속 요청: 직전 분석 SQL을 참고로 넘겨 같은 조건을 유지하게 합니다.
        previous_sql = str(
            state.get("previous_sql", "") or ""
        ).strip()
        if previous_sql:
            agent_input = (
                f"{question}\n\n"
                "[직전 분석 SQL — 후속 요청입니다. 사용자가 바꾼 조건만 바꾸고 나머지 조건은 유지하십시오]\n"
                f"{previous_sql}"
            )

        if retry_feedback:
            print("[Data Agent 재시도] 이전 SQL 결과를 반영해 다시 작성합니다.")
            agent_input = (
                f"{agent_input}\n\n"
                "[이전 시도 결과 — 반드시 반영해 SQL을 다시 작성]\n"
                f"{retry_feedback}"
            )

        result = data_agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=
                            agent_input
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


        # 등록부에 있는 출처만 남기고, SQL의 schema 이름으로 보정합니다.
        data_sources = [
            source.key
            for source in detect_sql_sources(
                plan.sql_query,
                getattr(plan, "data_sources", []) or [],
            )
        ]

        # -------------------------------------------------
        # 관계 / 상관관계 요청 보정
        # -------------------------------------------------
        #
        # Data Agent가 알려준 feature_columns를 기준으로
        #
        # 2개 지표 관계:
        #   전체 원천 데이터에서 분석 단위별 지표를 계산한 뒤
        #   최종 행만 최대 100,000개 시각화 표본으로 사용
        #
        # 3개 이상 지표 관계:
        #   전체 지표를 DuckDB corr()로 계산하고
        #   작은 correlation matrix만 pandas로 전달
        # -------------------------------------------------

        relation_features = clean_relation_features(
            getattr(
                plan,
                "feature_columns",
                [],
            )
        )

        # 사용자가 산점도를 명시하면 지표가 3개 이상이어도 요청을 우선합니다.
        explicit_scatter_requested = any(
            keyword in str(question or "").lower()
            for keyword in ("산점도", "scatter")
        )

        relation_requested = (
            has_relation_intent(question)
            or data_result_type == "correlation_matrix"
            or explicit_scatter_requested
        )

        if relation_requested and len(relation_features) >= 2:

            relation_sql_valid, missing_features = (
                validate_relation_sql_features(
                    sql_query=sql_query,
                    feature_columns=relation_features,
                )
            )

            if not relation_sql_valid:

                raise ValueError(
                    "관계 분석에 필요한 지표가 "
                    "Data Agent SQL에서 누락되었습니다: "
                    f"{missing_features}"
                )


        if (
            relation_requested
            and len(relation_features) >= 3
            and not explicit_scatter_requested
        ):
            # 표본 SQL로 전체 모집단 상관계수를 계산했다고 표시하지 않습니다.
            # 정확한 전체 상관계수가 필요하면 표본 제한 없이 재생성해야 합니다.
            if (
                re.search(
                    r"\b(?:LIMIT|TABLESAMPLE)\b|\bUSING\s+SAMPLE\b",
                    sql_query,
                    flags=re.IGNORECASE,
                )
                or data_is_sampled
            ):
                raise ValueError(
                    "상관행렬 계산에 표본 SQL이 사용되었습니다. "
                    "전체 데이터 기준 SQL로 다시 분석해야 합니다."
                )

            sql_query = (
                build_correlation_matrix_sql(
                    sql_query=sql_query,
                    feature_columns=relation_features,
                )
            )

            data_result_type = "correlation_matrix"
            data_uses_full_population = True
            data_is_sampled = False
            data_sample_size = 0
            data_sampling_reason = ""
            data_scope_note = (
                "지표는 전체 원천 데이터 기준으로 계산하고, "
                "DuckDB corr()로 전체 기준 상관계수를 계산한 뒤 "
                "작은 상관계수 행렬만 시각화 단계에 전달합니다."
            )


        elif relation_requested and (
            len(relation_features) == 2 or explicit_scatter_requested
        ):

            data_result_type = "visual_sample"
            data_uses_full_population = False
            data_is_sampled = True
            data_sample_size = MAX_VISUAL_SAMPLE_ROWS
            data_sampling_reason = (
                "두 지표의 관계를 점 단위로 표현하기 위해 "
                "전체 원천 데이터에서 분석 단위별 지표를 정확히 계산한 뒤 "
                "최종 행만 시각화용으로 표본 추출합니다."
            )
            data_scope_note = (
                "전체 원천 데이터 기준으로 지표를 계산한 뒤 "
                "관계 시각화를 위해 최대 "
                f"{MAX_VISUAL_SAMPLE_ROWS:,}개 행을 사용합니다."
            )


        elif (
            data_result_type == "correlation_matrix"
            and "corr(" not in sql_query.lower()
        ):

            # 상관행렬이라고 했지만 지표 목록도 corr()도 없으면
            # 원천 행을 대량으로 가져오지 않도록 실패 처리합니다.
            raise ValueError(
                "상관계수 행렬을 만들 지표 목록(feature_columns)이 부족합니다. "
                "비교할 숫자 지표를 3개 이상 지정해 다시 요청해 주세요."
            )


        # -------------------------------------------------
        # Top-N처럼 이미 작은 결과는 표본 추출하지 않음
        # -------------------------------------------------
        # LLM이 "그래프" 요청이라는 이유로 visual_sample을 고르면
        # ORDER BY … LIMIT 10 결과에 reservoir 표본이 씌워져 순위 순서가 섞입니다.
        # (예: 2등을 물었을 때 잘못된 행을 답할 수 있음)
        if (
            data_result_type == "visual_sample"
            and not (relation_requested and len(relation_features) == 2)
            and is_small_limited_result(sql_query)
        ):
            print("[Data 범위 보정] LIMIT으로 이미 작은 결과라 표본 추출 없이 전체 결과로 처리합니다.")
            data_result_type = "full_result"
            data_sampling_reason = ""
            data_scope_note = ""
            data_sample_size = 0


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

            "data_sources":
                data_sources,

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
    state: AnalysisState
) -> Command[
    Literal[
        "data_agent",
        "step_router",
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


    retry_count = int(
        state.get("data_retry_count", 0) or 0
    )

    # -------------------------------------------------
    # 0행 또는 실행 오류 → Data Agent에 1회 수정 기회
    # -------------------------------------------------
    # 필터 값 오타(예: 존재하지 않는 'Ladies')로 0행이 나오는 경우가 많아
    # WHERE에 쓰인 범주형 컬럼의 실제 값 목록을 함께 전달합니다.
    from agent_system.common_tools.sql_semantic_rules import semantic_issue

    issue = semantic_issue(
        state.get("question", ""),
        sql_query,
        len(df) if df is not None else -1,
    )

    needs_retry = (
        bool(issue)
        or (df is None and error and "SQL 실행 오류" in str(error))
        or (df is not None and len(df) == 0)
    )

    if needs_retry and retry_count < MAX_DATA_RETRY:

        if issue:
            feedback = (
                f"이전 SQL:\n{sql_query}\n\n"
                f"집계 단위 검증 실패: {issue}"
            )
        elif df is None:
            feedback = (
                f"이전 SQL:\n{sql_query}\n\n"
                f"실행 오류: {error}\n"
                "오류 원인을 고쳐 다시 작성하십시오."
            )
        else:
            feedback = (
                f"이전 SQL:\n{sql_query}\n\n"
                "결과가 0행이었습니다. WHERE 조건의 값이 실제 데이터에 없을 가능성이 큽니다.\n"
                "아래 실제 값 목록에서 질문 의도에 맞는 값을 골라 다시 작성하십시오."
            )

        hint = build_filter_value_hint(sql_query)
        if hint:
            feedback += f"\n\n[실제 값 목록]\n{hint}"

        print(
            f"[SQL Executor] "
            f"{'실행 오류' if df is None else '0행 결과'} → Data Agent 재시도 "
            f"({retry_count + 1}/{MAX_DATA_RETRY})"
        )

        return Command(

            update={
                "data_retry_count":
                    retry_count + 1,

                "data_retry_feedback":
                    feedback,
            },

            goto=
                "data_agent",
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
            "step_router",
    )


# =========================================================
# Prep Agent Node
# =========================================================

def prep_node(
    state: AnalysisState
) -> Command[
    Literal[
        "step_router",
        "final_response",
    ]
]:

    print(
        "[Prep Agent]"
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

        prep_agent = get_prep_agent(
            state.get("llm_config")
        )

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


    # -------------------------------------------------
    # 시간·개체 컬럼 결정
    # Prep Agent가 지정한 컬럼을 우선 쓰고,
    # 없으면 등록된 출처의 컬럼 후보(common/data_sources.py)로 자동 선택합니다.
    # -------------------------------------------------

    timestamp_column = (
        str(getattr(plan, "timestamp_column", "") or "").strip()
        or detect_timestamp_column(processed_df)
    )

    entity_column = (
        str(getattr(plan, "entity_column", "") or "").strip()
        or detect_entity_column(processed_df)
    )


    if (
        "convert_timestamp"
        in actions
    ):

        processed_df, report = (
            convert_timestamp(
                processed_df,
                timestamp_column=timestamp_column,
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
                processed_df,
                client_column=entity_column,
                timestamp_column=timestamp_column,
            )
        )


        action_reports[
            "sort_by_timestamp"
        ] = report


    if (
        "create_repeat_count_feature"
        in actions
    ):

        # 계획에서 지정한 그룹 컬럼은 누락 없이 그대로 사용
        requested_group_columns = list(
            getattr(plan, "repeat_group_columns", []) or []
        )

        if requested_group_columns:
            missing_columns = [
                column
                for column in requested_group_columns
                if column not in processed_df.columns
            ]
            if missing_columns:
                raise ValueError(
                    "반복 집계에 필요한 컬럼이 없습니다: "
                    + ", ".join(missing_columns)
                )

        group_columns = requested_group_columns

        if not group_columns:

            # 기본: 등록된 출처의 반복 기록 기준 (예: H&M 같은 날·같은 고객·같은 상품)
            group_columns = default_repeat_group_columns(
                processed_df
            )


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


    # 전처리 작업의 실패를 확인한 뒤 완료 상태를 기록합니다.
    failed_actions = []

    for action_name, action_report in action_reports.items():
        if (
            isinstance(action_report, dict)
            and action_report.get("success") is False
        ):
            error_detail = (
                action_report.get("error")
                or action_report.get("message")
                or "실패 원인이 제공되지 않았습니다."
            )
            failed_actions.append(
                f"{action_name}: {error_detail}"
            )

    if failed_actions:
        error_message = (
            "전처리 작업에 실패했습니다. "
            "완료 상태로 기록하지 않습니다. "
            + " | ".join(failed_actions)
        )
        return Command(
            update={
                "prep_error": error_message,
                "error": error_message,
                "prep_actions": actions,
                "data_quality_report": data_quality_report,
            },
            goto="final_response",
        )

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
            "step_router",
    )


# =========================================================
# Visualization Agent Node
# =========================================================

def visualization_node(
    state: AnalysisState
) -> Command[
    Literal[
        "step_router",
        "final_response",
    ]
]:

    print(
        "[Visualization Agent]"
    )



    df = get_analysis_dataframe(state)

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
        column
        for column in df.columns
        if is_semantic_identifier(str(column))
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

        visualization_agent = get_visualization_agent(
            state.get("llm_config")
        )

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

        if explicit_scatter:
            raise ValueError(
                "산점도 요청에 상관계수 행렬이 전달되었습니다. "
                "원본 지표 행을 조회하도록 다시 분석해야 합니다."
            )

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
            "step_router",
    )


# =========================================================
# Final Response Node
# =========================================================

def final_response_node(
    state: AnalysisState
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

        if str(error).startswith(SOURCE_UNAVAILABLE_PREFIX):

            # 수집 중이라 잠긴 출처: SQL 문제가 아니므로 안내 문구만 보여 줍니다.
            final_answer = str(error)

        else:

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


            # 차트 메타데이터 대신 사용자에게 자연스러운 결과 안내를 표시합니다.
            chart_type_labels = {
                "bar": "막대그래프",
                "line": "선그래프",
                "histogram": "히스토그램",
                "boxplot": "상자그림",
                "scatter": "산점도",
                "heatmap": "히트맵",
            }
            chart_type = visualization_result.get("chart_type", "")
            chart_title = str(visualization_result.get("title", "") or "분석 결과")
            chart_name = chart_type_labels.get(chart_type, "그래프")

            final_answer = (
                f"{chart_title}\n\n"
                f"요청하신 데이터를 분석해 {chart_name}로 정리했습니다.\n"
            )

            # 데이터가 반환되면 실제 숫자에 근거한 간단한 요약을 구성합니다.
            # 고정 질문/카테고리를 사용하지 않고 DataFrame 구조로 판단합니다.
            analysis_df = get_analysis_dataframe(state)
            if isinstance(analysis_df, pd.DataFrame) and not analysis_df.empty:
                metric_columns = [
                    col for col in analysis_df.select_dtypes(include="number").columns
                    if not is_semantic_identifier(col)
                ]
                label_columns = [
                    col for col in analysis_df.columns
                    if col not in metric_columns
                    and not is_semantic_identifier(col)
                ]
                if (
                    metric_columns
                    and label_columns
                    and chart_type == "bar"
                    and visualization_result.get("x_column") in label_columns
                    and visualization_result.get("y_column") in metric_columns
                ):
                    # 그래프에 실제 사용한 Y축 지표를 그대로 요약
                    metric = visualization_result["y_column"]
                    label_column = visualization_result["x_column"]
                    top = analysis_df[[label_column, metric]].dropna().sort_values(
                        metric, ascending=False
                    ).head(3)
                    if not top.empty:
                        top_lines = []
                        for _, item in top.iterrows():
                            value = item[metric]
                            display = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
                            top_lines.append(f"- {item[label_column]}: {display}")
                        final_answer += "\n주요 결과 (상위 3개)\n" + "\n".join(top_lines) + "\n"

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


            # 샘플링 정책·DataFrame 크기 등 개발용 설명은 최종 메시지에서 제외합니다.
            # 관련 상세 정보는 State 및 터미널 실행 로그에 그대로 남아 있습니다.

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
        # 예측 모델 요청 (현재 미지원)
        # =================================================

        elif (
            state.get("supervisor_reason", "")
            == UNSUPPORTED_PREDICTION_REASON
        ):

            final_answer = (
                "요청하신 작업은 학습된 예측 모델이 필요합니다.\n"
                "현재 CART SIGNAL은 예측 모델 기능을 제공하지 않고, "
                f"{'·'.join(source.label for source in get_available_sources())} "
                "데이터의 조회·집계·전처리·시각화만 지원합니다.\n\n"
                "[이렇게 바꿔 물어보실 수 있습니다]\n"
                + "\n".join(
                    f"- {question}"
                    for source in get_available_sources()
                    for question in source.example_questions
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


                if row_count == 0:

                    final_answer = (
                        f"{state.get('data_purpose', '데이터 조회')}\n\n"
                        "조회 결과: 조건에 맞는 데이터가 없습니다 (0행).\n\n"
                        "필터 값이 실제 데이터와 다를 수 있습니다. "
                        "아래 사용된 SQL의 조건을 확인하시거나, "
                        "대상(예: 여성/남성/아동)이나 상품 유형을 조금 넓혀 다시 질문해 주세요."
                    )

                else:

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


# 이전 함수 import와의 일시적 호환성을 위한 별칭 (Graph 노드명과 무관)
orchestrator_node = step_router_node  # 이전 이름 호환
supervisor_node = step_router_node
