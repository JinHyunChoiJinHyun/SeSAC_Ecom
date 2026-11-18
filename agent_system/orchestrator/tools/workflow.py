"""Orchestrator Tool — manage_workflow: 대화 단계에 따라 실행 흐름 제어 (Conditional Edge + 워크플로 실행).

그래프 연결
    orchestrator ──after_orchestrator──▶ <특화 Agent> | workflow | check_approval | END
    <특화 Agent>  ──after_agent─────────▶ check_approval | END
    workflow     ──after_workflow──────▶ check_approval | END
    check_approval ──after_approval────▶ workflow (선택 대기 중인 값이 승인되면 다음 단계 진행) | END

워크플로 (agent_system/workflows/, 아키텍처 원칙 5 혼합형 오케스트레이션)
    - 대화 판단(intent)이 "기획전 준비 전체 진행" 같은 요청을 workflow로 판단하면 run을 만들고 workflow 노드로 보냄
    - run_workflow_node가 단계 순서대로 전문 Agent를 실행하다가
        마케터 선택(select)  → 질문을 남기고 멈춤 (status=waiting_input)
        담당 Agent 구현 전   → optional이면 건너뜀, 아니면 멈춤 (status=blocked)
        팀장 승인 등 gate    → 멈춤 (status=blocked)
    - 마케터가 값을 고르면 대화 판단이 decision으로 받아 확정값 승인(check_approval)을 거치고,
      승인되면 after_approval이 workflow 노드로 보내 다음 단계를 이어서 실행
    - 대기 중 다른 질문은 평소처럼 답하고 끝에 현재 단계 안내를 붙임 (같은 단계로 복귀)
    - 명령: "워크플로 계속"(멈춘 단계 다시 시도) · "워크플로 상태" · "워크플로 취소"
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage
from langgraph.graph import END

from agent_system.orchestrator.registry import AgentRequest, get_agent, normalize_agent_result
from agent_system.workflows import base
from agent_system.workflows.registry import get_workflow

CHECK_APPROVAL = "check_approval"
WORKFLOW_NODE = "workflow"
OWNER_LABELS = {"marketer": "마케터", "team_lead": "팀장"}
PREVIOUS_CHARS = 1500           # 다음 단계 안내문 {previous}에 넣을 직전 결과 길이
MAX_HISTORY = 40

# 진행 중인 워크플로 명령 (규칙 판단, LLM 없음)
CANCEL_COMMAND = re.compile(
    r"^\s*(?:워크플로|기획전\s*준비)\s*"
    r"(?:전체\s*)?(?:취소|중단|종료|그만)"
    r"(?:해\s*줘|해\s*주세요|해주세요|해줘|할게|하자|해)?"
    r"\s*[.!?~]*\s*$"
)

# 메시지 전체가 계속 명령일 때만 재개 (CodeRabbit 리뷰: "워크플로 계속하지 마"·"워크플로 계속해도 돼?"는 명령 아님)
CONTINUE_COMMAND = re.compile(
    r"^\s*(?:워크플로|기획전\s*준비)\s*(?:를\s*|을\s*)?"
    r"(?:계속|재개|이어서|이어\s*가기|다시\s*시도)\s*"
    r"(?:진행\s*)?(?:해\s*줘|해\s*주세요|해주세요|해줘|할게|하자|해|하기)?"
    r"\s*[.!~]*\s*$"
)
STATUS_COMMAND = re.compile(
    r"(?:워크플로|기획전\s*준비)"
    r".*?(?:현재\s*)?(?:상태|어디까지|진행\s*상황|몇\s*단계)"
)


# ---------------------------------------------------------------- 조건부 엣지
def after_orchestrator(state) -> str:
    if state.get("turn_type") == "workflow" and base.is_active(state.get("workflow_run")):
        return WORKFLOW_NODE
    if state.get("turn_type") == "analysis" and state.get("route"):
        return state["route"]
    if state.get("pending_approval"):
        return CHECK_APPROVAL
    return END


def after_agent(state) -> str:
    return CHECK_APPROVAL if state.get("pending_approval") else END


def after_workflow(state) -> str:
    return CHECK_APPROVAL if state.get("pending_approval") else END


def waiting_value_confirmed(run: dict | None, campaign: dict | None) -> bool:
    """선택 대기 중인 확정값이 run 시작 이후 확정됐는지."""
    run = run or {}
    return (run.get("status") == "waiting_input" and bool(run.get("waiting_key"))
            and base.confirmed_since(campaign, run["waiting_key"], run.get("started_at", "")))


def after_approval(state) -> str:
    approved = (state.get("approval_result") or {}).get("kind") == "approve"
    if approved and waiting_value_confirmed(state.get("workflow_run"), state.get("campaign")):
        return WORKFLOW_NODE
    return END


# ---------------------------------------------------------------- 안내 문구
def owner_label(owner: str) -> str:
    if owner in OWNER_LABELS:
        return OWNER_LABELS[owner]
    spec = get_agent(owner)
    return f"{spec.label} Agent" if spec else owner


def current_step(run: dict | None):
    workflow = get_workflow((run or {}).get("workflow", ""))
    if not workflow:
        return None, None, None
    steps = workflow.steps()
    index = int((run or {}).get("step_index", 0))
    if 0 <= index < len(steps):
        stage, step = steps[index]
        return workflow, stage, step
    return workflow, None, None


def run_status_text(run: dict | None, campaign: dict | None = None) -> str:
    """대화 판단 입력·답변 끝 안내에 쓰는 현재 단계 설명. 진행 중인 run이 없으면 빈 문자열."""
    if not run or run.get("status") in ("done", "cancelled", None):
        return ""
    workflow, stage, step = current_step(run)
    if not workflow or step is None:
        return ""
    head = f"{workflow.title} · {stage.title} · {step.no}단계 {step.title} ({owner_label(step.owner)})"
    if run.get("status") == "waiting_input":
        return (f"{head} — 선택 대기 중. {base.render(step.instruction, run, campaign)}\n"
                f"(값을 말씀하시면 확정값 '{step.selects}'로 승인받은 뒤 다음 단계로 진행합니다)")
    if run.get("status") == "blocked":
        return f"{head} — 멈춤: {run.get('blocked_reason', '')} ('워크플로 계속'으로 다시 시도, '워크플로 취소'로 종료)"
    return f"{head} — 진행 중"


def workflow_reminder(run: dict | None, campaign: dict | None = None) -> str:
    text = run_status_text(run, campaign)
    return f"[진행 중인 워크플로] {text}" if text and (run or {}).get("status") == "waiting_input" else ""


# ---------------------------------------------------------------- 실행
def _history(run: dict, step, status: str, summary: str = "") -> list:
    item = {"no": step.no, "title": step.title, "owner": step.owner, "status": status, "summary": summary[:300]}
    return (list(run.get("history") or []) + [item])[-MAX_HISTORY:]


def _run_agent_step(step, run: dict, state) -> dict:
    spec = get_agent(step.owner)
    request = AgentRequest(
        question=base.render(step.instruction, run, state.get("campaign")),
        user_question=run.get("request", ""),
        is_followup=False,
        last_analysis={},
        llm_config=state.get("llm_config"),
        campaign=state.get("campaign") or {},
    )
    try:
        return normalize_agent_result(spec.name, spec.load_runner()(request))
    except Exception as exc:  # Agent 실패는 그 단계에서 멈추고 '워크플로 계속'으로 다시 시도
        return {"final_answer": f"{spec.label} Agent 실행에 실패했습니다.", "error": f"{type(exc).__name__}: {exc}"}


def run_workflow_node(state) -> dict:
    """현재 run을 다음 멈출 지점(마케터 선택·구현 전 Agent·gate·완료)까지 실행합니다."""
    from agent_system.orchestrator.orchestrator import store_turn_result
    from agent_system.orchestrator.tools import context

    run = dict(state.get("workflow_run") or {})
    campaign = state.get("campaign") or {}
    workflow = get_workflow(run.get("workflow", ""))
    if not workflow or not base.is_active(run):
        return {}

    steps = workflow.steps()
    index = int(run.get("step_index", 0))
    sections, results, last_spec, last_question = [], [], None, ""
    from agent_system.common_tools.report_export import analysis_log as report_log
    log = list(state.get("analysis_log") or [])
    run["status"] = "running"
    run["waiting_key"] = ""

    while index < len(steps):
        stage, step = steps[index]
        if stage.key != run.get("stage"):
            missing = [k for k in stage.requires if not ((campaign.get("values") or {}).get(k))]
            if missing:
                run.update(status="blocked", blocked_reason=f"{stage.title}에 필요한 확정값 없음: {missing}")
                sections.append(f"■ {stage.title}: 필요한 확정값({', '.join(missing)})이 없어 멈춥니다.")
                break
            run["stage"] = stage.key
            run = base.record_run_event(run, "stage_transition", stage=stage.key)

        label = f"■ {step.no}단계 {step.title} — {owner_label(step.owner)}"
        if step.kind == "select":
            if base.confirmed_since(campaign, step.selects, run.get("started_at", "")):
                value = (campaign.get("values") or {}).get(step.selects, "")
                sections.append(f"{label}\n확정: {value}")
                run["history"] = _history(run, step, "done", f"확정: {value}")
                index += 1
                continue
            run.update(status="waiting_input", waiting_key=step.selects)
            sections.append(f"{label}\n{base.render(step.instruction, run, campaign)}")
            run = base.record_run_event(run, "waiting_input", step=step.no, key=step.selects)
            break

        if step.kind == "gate":
            run.update(status="blocked", blocked_reason=f"{step.no}단계 {step.title}은(는) 승인 기능 구현 전")
            sections.append(f"{label}\n{step.instruction} (승인 기록 기능 구현 전이라 여기서 멈춥니다)")
            break

        spec = get_agent(step.owner)
        if spec is None or not spec.enabled:
            name = spec.label if spec else step.owner
            if step.optional:
                sections.append(f"{label}\n건너뜀: {name} Agent 구현 전 (이전 단계 결과 표로 대신)")
                run["history"] = _history(run, step, "skipped")
                index += 1
                continue
            run.update(status="blocked", blocked_reason=f"{name} Agent 구현 전")
            sections.append(f"{label}\n{name} Agent가 아직 구현되지 않아 여기서 멈춥니다. "
                            "구현 후 '워크플로 계속'이라고 하면 이 단계부터 이어서 진행합니다.")
            run = base.record_run_event(run, "blocked", step=step.no, reason="agent_not_implemented")
            break

        result = _run_agent_step(step, run, state)
        if result.get("not_ready"):
            # Agent는 켜져 있지만 데이터·모델·백엔드 연결 전 (가짜 결과 없이 미구현 안내만 돌려줌)
            run = base.record_run_event(run, "not_ready", step=step.no, agent=spec.name)
            if step.optional:
                sections.append(f"{label}\n건너뜀: {spec.label} Agent 미구현 (연결 전)")
                run["history"] = _history(run, step, "skipped")
                index += 1
                continue
            run.update(status="blocked", blocked_reason=f"{spec.label} Agent 미구현 (연결 전)")
            sections.append(f"{label}\n{str(result.get('final_answer', '')).strip()}")
            break
        answer = str(result.get("final_answer", "")).strip()
        sections.append(f"{label}\n{answer}")
        run = base.record_run_event(run, "agent_call", step=step.no, agent=spec.name,
                                    error=str(result.get("error") or ""))
        if result.get("error"):
            run.update(status="blocked", blocked_reason=f"{step.no}단계 실패: {result['error']}")
            run["history"] = _history(run, step, "failed", str(result["error"]))
            sections.append("이 단계에서 멈춥니다. '워크플로 계속'이라고 하면 다시 시도합니다.")
            break
        results.append(result)
        last_spec, last_question = spec, base.render(step.instruction, run, campaign)
        log = report_log.append(log, report_log.make_entry(   # 기획전 실행 계획 보고서 재료
            result, entry_id=f"{state['turn_id']}_{index}", question=last_question,
            user_question=f"{step.no}단계 {step.title}", route=spec.name, route_label=spec.label,
            kind="workflow_step", workflow={"run_id": run["run_id"], "workflow": workflow.name,
                                            "title": workflow.title, "step_no": step.no, "step_title": step.title}))
        run["previous"] = context.memory_answer(answer)[:PREVIOUS_CHARS]
        run["history"] = _history(run, step, "done", run["previous"])
        index += 1

    run["step_index"] = index
    if index >= len(steps) and run.get("status") == "running":
        run["status"] = "done"
        sections.append(f"■ {workflow.title} 워크플로를 모두 마쳤습니다.")
        run = base.record_run_event(run, "done")

    prefix = str(state.get("reply") or "").strip() if state.get("turn_type") in ("approval", "workflow") else ""
    header = f"[{workflow.title} 워크플로 · run {run['run_id']}]"
    final_answer = "\n\n".join(([prefix] if prefix else []) + [header] + sections)
    turn_result = {
        "final_answer": final_answer,
        "turn_type": "workflow",
        "workflow_run": {k: run.get(k) for k in ("run_id", "workflow", "stage", "status", "step_index", "waiting_key")},
        "data_sources": sorted({s for r in results for s in (r.get("data_sources") or [])}),
        "data_purpose": f"워크플로 {workflow.title}: " + ", ".join(
            h["title"] for h in run.get("history", []) if h["status"] == "done") or "",
        "completed_steps": [f"{workflow.name}:{h['no']}" for h in run.get("history", []) if h["status"] == "done"],
        "awaiting_input": run["status"] == "waiting_input",
    }
    # Slack 표·그래프: 마지막으로 표·그래프를 만든 단계 결과
    table_result = next((r for r in reversed(results) if r.get("dataframe") is not None), None)
    if table_result is not None:
        turn_result["dataframe"] = table_result["dataframe"]
    chart_result = next((r for r in reversed(results) if r.get("visualization_path")), None)
    if chart_result is not None:
        turn_result["visualization_path"] = chart_result["visualization_path"]
        turn_result["visualization_result"] = chart_result.get("visualization_result")
    store_turn_result(state["turn_id"], turn_result)

    update = {
        "workflow_run": run,
        "analysis_log": log,
        "turn_type": "workflow",
        "reply": final_answer,
        "messages": [AIMessage(content=context.memory_answer(final_answer), name=WORKFLOW_NODE)],
    }
    if results:   # 후속 질문("그 후보들 비교해줘")이 마지막 Agent 결과를 이어받도록
        update["last_analysis"] = context.summarize_analysis(last_question, results[-1], last_spec)
    return update


# ---------------------------------------------------------------- 시작·명령 (orchestrator_node에서 사용)
def start_run(workflow_name: str, request: str, campaign: dict | None, previous_run: dict | None) -> tuple[dict, str]:
    """새 run 만들기. 반환: (run, 시작할 수 없을 때 안내문)"""
    workflow = get_workflow(workflow_name)
    if workflow is None:
        return {}, f"등록되지 않은 워크플로입니다: {workflow_name}"
    if base.is_active(previous_run):
        return {}, ("이미 진행 중인 워크플로가 있습니다.\n" + run_status_text(previous_run, campaign)
                    + "\n새로 시작하려면 먼저 '워크플로 취소'라고 말씀해 주세요.")
    missing = [k for k in workflow.requires if not ((campaign or {}).get("values") or {}).get(k)]
    if missing:
        return {}, (f"'{workflow.title}'을(를) 시작하려면 먼저 확정값이 필요합니다: {', '.join(missing)}\n"
                    "메인 기획전 워크플로에서 주력 상품을 확정한 뒤 다시 요청해 주세요.")
    parent = (previous_run or {}).get("run_id", "") if (previous_run or {}).get("workflow") == workflow.parent else ""
    run = base.record_run_event(base.new_run(workflow, request, parent_run_id=parent), "run_started")
    return run, ""


def handle_command(message: str, run: dict | None, campaign: dict | None) -> tuple[str, dict | None, bool]:
    """'워크플로 취소/계속/상태' 명령 처리. 반환: (답변, 바뀐 run 또는 None, 계속 실행할지)"""
    if not run or run.get("status") in ("done", "cancelled"):
        if CANCEL_COMMAND.search(message) or CONTINUE_COMMAND.search(message) or STATUS_COMMAND.search(message):
            return "진행 중인 워크플로가 없습니다.", None, False
        return "", None, False
    if CANCEL_COMMAND.search(message):
        cancelled = base.record_run_event({**run, "status": "cancelled", "waiting_key": ""}, "cancelled")
        return f"워크플로를 취소했습니다. (run {run.get('run_id')}) 확정값은 그대로 둡니다.", cancelled, False
    if CONTINUE_COMMAND.search(message):
        if run.get("status") == "waiting_input":
            return run_status_text(run, campaign), None, False
        resumed = base.record_run_event({**run, "status": "running", "blocked_reason": ""}, "resumed")
        return "", resumed, True
    if STATUS_COMMAND.search(message):
        done = [f"{h['no']}단계 {h['title']}({h['status']})" for h in run.get("history", [])]
        return ("[워크플로 상태] " + run_status_text(run, campaign)
                + ("\n지난 단계: " + ", ".join(done) if done else "")), None, False
    return "", None, False
