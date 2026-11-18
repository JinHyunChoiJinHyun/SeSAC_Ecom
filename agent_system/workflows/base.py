"""워크플로 공통 구조 — Workflow · Stage · Step, 단계별 필수 확정값 (아키텍처 workflows/base.py).

아키텍처 원칙 5 (혼합형 오케스트레이션)
    route_request는 "어떤 워크플로인가"까지만 정하고, 단계 순서·마케터 선택·승인은 워크플로가 고정합니다.
    단계 중간에 들어온 다른 질문은 평소처럼 답하고, 같은 단계로 돌아옵니다.

Step 종류 (kind)
    agent   전문 Agent 실행. 담당 Agent가 아직 구현 전(registry enabled=False)이면
            optional=True → 건너뛰고 안내, optional=False → 그 단계에서 멈춤(blocked)
    select  마케터 선택 대기. 채팅으로 고른 값을 확정값 승인(check_approval)으로 저장하면 다음 단계로 진행
            (selects = 저장할 확정값 key, conversation_state.CAMPAIGN_KEYS 중 하나)
    gate    사람이 시스템 밖에서 처리하는 단계 (예: 팀장 승인 버튼). 담당 기능 구현 전이라 지금은 멈춤

진행 상태(run)는 지금 LangGraph state["workflow_run"]에 저장합니다 (사용자 결정 11/18).
아키텍처상 원본은 PostgreSQL run 테이블이므로, 백엔드 run API가 생기면 record_run_event()에서 보내면 됩니다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Step:
    no: int                      # 시나리오 문서의 단계 번호 (메인 1~29, 서브 1~4)
    title: str                   # 단계 이름 (안내·로그)
    kind: str = "agent"          # agent / select / gate
    owner: str = ""              # 담당: Agent 이름(registry) 또는 "marketer" · "team_lead"
    instruction: str = ""        # agent: Agent에 넘길 요청 (아래 placeholder 사용)
                                 # select: 마케터에게 보여 줄 질문
    selects: str = ""            # select: 저장할 확정값 key
    optional: bool = False       # agent: 담당 Agent가 없으면 건너뛰어도 되는 단계 (예: 시각화)
    note: str = ""               # 문서·안내용 메모 (아키텍처와 시나리오 차이 등)


@dataclass(frozen=True)
class Stage:
    key: str                     # 예: s1_product_selection
    title: str
    steps: tuple[Step, ...]
    requires: tuple[str, ...] = ()   # 이 단계에 들어가기 전에 있어야 하는 확정값


@dataclass(frozen=True)
class Workflow:
    name: str                    # route_request가 고르는 이름
    title: str
    description: str             # 대화 판단 LLM이 시작 여부를 판단할 설명
    examples: tuple[str, ...]
    stages: tuple[Stage, ...]
    requires: tuple[str, ...] = ()   # 시작 전 필요한 확정값 (서브 워크플로: 메인 확정값)
    parent: str = ""             # 서브 워크플로면 메인 워크플로 이름 (parent_run_id용)

    def steps(self) -> list[tuple[Stage, Step]]:
        return [(stage, step) for stage in self.stages for step in stage.steps]

    def owners(self) -> set[str]:
        return {step.owner for _, step in self.steps() if step.kind == "agent"}


# 단계 안내문에서 쓸 수 있는 값: {request} {previous} + 확정값 key ({category} {products} …)
PLACEHOLDER_HELP = "{request}=시작 요청 원문, {previous}=직전 단계 결과 요약, {category}·{products}… = 확정값"


class _Blank(dict):
    def __missing__(self, key):
        return "(미정)"


def render(template: str, run: dict, campaign: dict | None) -> str:
    values = dict((campaign or {}).get("values") or {})
    values.update(request=run.get("request", ""), previous=run.get("previous", "") or "(없음)")
    return str(template or "").format_map(_Blank(values))


# ---------------------------------------------------------------- run 상태
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run(workflow: Workflow, request: str, parent_run_id: str = "") -> dict:
    """워크플로 1회 실행(run) 상태. step_index는 workflow.steps()의 위치."""
    return {
        "run_id": uuid.uuid4().hex[:12],
        "parent_run_id": parent_run_id,
        "workflow": workflow.name,
        "request": request,
        "status": "running",        # running / waiting_input / blocked / done / cancelled
        "step_index": 0,
        "stage": workflow.stages[0].key,
        "waiting_key": "",
        "started_at": _now(),
        "previous": "",
        "history": [],               # [{"no", "title", "owner", "status", "summary"}] 최근 40건
        "events": [],
    }


def is_active(run: dict | None) -> bool:
    return bool(run) and run.get("status") in ("running", "waiting_input")


def confirmed_since(campaign: dict | None, key: str, since: str) -> bool:
    """run 시작 이후에 key가 확정됐는지 (campaign log 기준)."""
    for item in (campaign or {}).get("log") or []:
        if item.get("key") == key and str(item.get("at", "")) >= str(since or ""):
            return True
    return False


def record_run_event(run: dict, event_type: str, **payload) -> dict:
    """run_event 기록 (아키텍처 [2-2] run_event). 지금은 로그 + state에만 남깁니다.

    백엔드 run API가 준비되면 여기서 backend_client.call("POST", "/runs/{run_id}/events", …)로 보내면 됩니다.
    """
    event = {"at": _now(), "type": event_type, **payload}
    print(f"[워크플로] {run.get('workflow')} run={run.get('run_id')} {event_type} {payload}")
    events = list(run.get("events") or []) + [event]
    return {**run, "events": events[-60:]}
