from langgraph.graph import (
    StateGraph,
    START
)

from state import AgentState

from nodes import (
    supervisor_node,
    data_node,
    sql_executor_node,
    prep_node,
    model_node,
    experiment_node,
    visualization_node,
    final_response_node,
)


# =========================================================
# Graph 생성
# =========================================================

graph_builder = StateGraph(
    AgentState
)


# =========================================================
# Node 등록
# =========================================================

# Supervisor Agent
graph_builder.add_node(
    "supervisor",
    supervisor_node
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
# 아직 실제 기능은 구현하지 않았지만
# Graph 구조를 위해 Node는 등록
graph_builder.add_node(
    "prep_agent",
    prep_node
)


# Model Agent
graph_builder.add_node(
    "model_agent",
    model_node
)


# Experiment Agent
graph_builder.add_node(
    "experiment_agent",
    experiment_node
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
    "supervisor"
)


# =========================================================
# Graph Compile
# =========================================================

# 이후 Agent 간 이동은
# 각 Node의 Command(goto="...")에서 결정
graph = graph_builder.compile()


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