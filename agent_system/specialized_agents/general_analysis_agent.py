"""
General Analysis Agent (기본 Agent)

등록된 데이터 출처(common/data_sources.py, 현재 H&M·Amazon)의 일반 조회·전처리·시각화 요청을 처리한다.

내부적으로는 공통 분석 그래프(common_tools.graph)를 재사용한다.
흐름: Step Router → Data → SQL Executor → (Prep) → (Visualization) → Final Response

특화 Agent 공통 규칙 (agent_system/orchestrator/registry.py)
- run(request: AgentRequest) -> dict 함수를 제공한다.
- 결과 dict는 공통 결과 형식(final_answer 필수, dataframe·visualization_path 등 선택)을 따른다.
"""

from langchain_core.messages import HumanMessage

from agent_system.common_tools.graph import graph as shared_analysis_graph


def get_general_analysis_graph():
    """General Analysis Agent가 사용할 공통 분석 그래프를 반환한다."""
    return shared_analysis_graph


def run(request) -> dict:
    """Orchestrator가 호출하는 실행 함수.

    후속 질문이면 직전 SQL을 Data Agent에 참고로 넘겨, 사용자가 바꾼 조건만 바꾸게 한다.
    """

    previous_sql = ""
    if request.is_followup:
        previous_sql = str((request.last_analysis or {}).get("sql", "") or "")

    result = get_general_analysis_graph().invoke(
        {
            "messages": [HumanMessage(content=request.question)],
            "question": request.question,
            "previous_sql": previous_sql,
            "llm_config": request.llm_config,
            "completed_steps": [],
        },
        config={"recursion_limit": 30},
    )
    return dict(result)
