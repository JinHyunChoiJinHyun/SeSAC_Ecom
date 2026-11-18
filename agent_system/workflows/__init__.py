"""워크플로 — 시나리오별 단계 순서와 조건 (아키텍처 agent_system/workflows/).

    base.py                   Workflow · Stage · Step 공통 구조, run 상태, 단계 안내문 채우기
    registry.py               워크플로 이름 → Workflow
    trend_planning/           메인 시나리오 (s1~s4)
    photography_planning.py   서브 시나리오

실행은 Orchestrator 그래프의 workflow 노드(orchestrator/tools/workflow.py run_workflow_node)가 합니다.
"""
