from langgraph.graph import (
    StateGraph,
    START
)

from .analysis_state import AnalysisState

from .nodes import (
    step_router_node,
    data_node,
    sql_executor_node,
    prep_node,
    visualization_node,
    final_response_node,
)


# =========================================================
# Graph 생성
# =========================================================

graph_builder = StateGraph(
    AnalysisState
)


# =========================================================
# Node 등록
# =========================================================

# Step Router (분석 단계 선택: 최상위 Orchestrator와 다름)
graph_builder.add_node(
    "step_router",
    step_router_node
)


# Data Agent
graph_builder.add_node(
    "data_agent",
    data_node
)


# SQL 실행 Node
graph_builder.add_node(
    "sql_executor",
    sql_executor_node
)


# Prep Agent
graph_builder.add_node(
    "prep_agent",
    prep_node
)


# Visualization Agent
graph_builder.add_node(
    "visualization_agent",
    visualization_node
)


# 최종 응답 Node
graph_builder.add_node(
    "final_response",
    final_response_node
)


# =========================================================
# 시작점
# =========================================================

graph_builder.add_edge(
    START,
    "step_router"
)


# =========================================================
# Graph Compile
# =========================================================

# 이후 Agent 간 이동은
# 각 Node의 Command(goto="...")에서 결정
#
# checkpointer=False: 대화 그래프(멀티턴) 안에서 실행돼도 부모의 대화 기억 저장소에
# 이 그래프의 State(DataFrame 포함)를 저장하지 않습니다.
# 대화 기억은 agent_system/orchestrator 그래프가 요약만 저장합니다.
graph = graph_builder.compile(checkpointer=False)


# =========================================================
# Graph 이미지 생성 테스트
# =========================================================

if __name__ == "__main__":

    png_bytes = (
        graph
        .get_graph()
        .draw_mermaid_png()
    )

    with open(
        "graph.png",
        "wb"
    ) as f:

        f.write(
            png_bytes
        )

    print(
        "graph.png 생성 완료"
    )