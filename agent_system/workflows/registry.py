"""워크플로 등록부 — route_request 결과(워크플로 이름) → Workflow (결정적 매핑, 아키텍처 workflows/registry.py).

    trend_planning         메인: 트렌드 기반 기획전 (원본 1~29단계)
    photography_planning   서브: 촬영·콘텐츠 기획 (4단계, 메인 확정값 이어받음)
"""

from __future__ import annotations

from agent_system.workflows.base import Workflow
from agent_system.workflows.photography_planning import WORKFLOW as PHOTOGRAPHY
from agent_system.workflows.trend_planning.workflow import WORKFLOW as TREND_PLANNING

WORKFLOWS: dict[str, Workflow] = {wf.name: wf for wf in (TREND_PLANNING, PHOTOGRAPHY)}


def get_workflow(name: str) -> Workflow | None:
    return WORKFLOWS.get(str(name or "").strip())


def describe_workflows() -> str:
    """대화 판단 프롬프트용 목록."""
    lines = []
    for wf in WORKFLOWS.values():
        lines.append(f"- {wf.name} ({wf.title}): {wf.description}")
        lines += [f"  예시: {e}" for e in wf.examples]
    return "\n".join(lines)
