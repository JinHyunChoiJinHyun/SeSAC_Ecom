"""멀티턴 대화 그래프 — LangGraph StateGraph + Checkpointer + thread_id + interrupt.

    START → orchestrator ──(manage_workflow: 조건부 엣지)──▶ <특화 Agent> ──▶ check_approval | END
                                                          ├▶ check_approval (승인 대기, interrupt)
                                                          └▶ END (일상 대화)
            check_approval → END

멀티턴 방식
- 그래프를 Checkpointer(SQLite, runtime/conversation_memory.sqlite)와 함께 compile 합니다.
- 실행할 때 config={"configurable": {"thread_id": ...}} 를 넘기면 LangGraph가 같은 thread_id의 이전 상태를
  불러오고, 실행이 끝나면 새 상태를 저장합니다.
- check_approval 노드가 interrupt()를 호출하면 그래프가 멈춘 상태로 저장됩니다.
  다음 메시지에서 run_turn()이 멈춘 상태를 확인해, 승인 답이면 Command(resume=...)로 이어서 실행합니다.

기억(checkpoint)에 저장하는 것
- messages         : 사용자 질문과 봇 답변 (최근 40개)
- last_analysis    : 직전 분석 요약 (후속 질문용)
- campaign         : 확정된 기획전 정보 (상품·타깃·일정 등) + 변경 기록
- pending_approval : 승인 대기 중인 요청
"""

import uuid

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from agent_system.orchestrator import settings
from agent_system.orchestrator.orchestrator import make_agent_node, orchestrator_node, pop_turn_result
from agent_system.orchestrator.registry import get_agents
from agent_system.orchestrator.tools import approval, context, workflow
from agent_system.orchestrator.tools.memory import get_checkpointer, langsmith_enabled, prune_thread_history


class OrchestratorState(MessagesState):
    llm_config: dict
    turn_id: str
    user_question: str
    turn_type: str               # chitchat / analysis / approval_request / approval
    reply: str                   # Orchestrator가 직접 한 답변
    standalone_question: str     # Agent에 넘긴 재작성 질문
    is_followup: bool
    conversation_reason: str     # 대화 판단 이유
    last_analysis: dict          # 직전 분석 요약 (다음 턴에서 참고)
    last_activity_at: str        # 마지막 메시지 시각 (UTC ISO). 자동 리셋 판단용
    was_reset: bool              # 이번 턴에 자동 리셋이 일어났는지
    route: str                   # 선택한 특화 Agent 이름
    route_reason: str            # 선택 이유
    campaign: dict               # 확정된 기획전 정보 (update_conversation_state)
    pending_approval: dict       # 승인 대기 요청 (check_approval)
    approval_result: dict        # 마지막 승인 처리 결과


def build_graph(checkpointer=None):
    """Orchestrator 그래프를 만듭니다. checkpointer가 있으면 멀티턴 기억이 켜집니다."""

    agents = get_agents()
    builder = StateGraph(OrchestratorState)
    builder.add_node("orchestrator", orchestrator_node)
    for spec in agents:
        builder.add_node(spec.name, make_agent_node(spec))
    builder.add_node(workflow.CHECK_APPROVAL, approval.check_approval_node)

    builder.add_edge(START, "orchestrator")
    # manage_workflow: 조건부 엣지
    builder.add_conditional_edges(
        "orchestrator", workflow.after_orchestrator,
        [spec.name for spec in agents] + [workflow.CHECK_APPROVAL, END],
    )
    for spec in agents:
        builder.add_conditional_edges(spec.name, workflow.after_agent, [workflow.CHECK_APPROVAL, END])
    builder.add_edge(workflow.CHECK_APPROVAL, END)
    return builder.compile(checkpointer=checkpointer)


_graph = None


def get_graph():
    """SQLite 기억이 연결된 Orchestrator 그래프 (프로세스당 1개)."""

    global _graph
    if _graph is None:
        _graph = build_graph(get_checkpointer())
    return _graph


def pending_interrupt(graph, thread_id: str):
    """thread가 check_approval에서 멈춰 있으면 interrupt 값을, 아니면 None."""

    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return None
    interrupts = list(getattr(snapshot, "interrupts", ()) or ())
    if not interrupts:
        for task in getattr(snapshot, "tasks", ()) or ():
            interrupts.extend(getattr(task, "interrupts", ()) or ())
    return interrupts[0].value if interrupts else None


def _interrupt_text(output: dict) -> str:
    items = output.get("__interrupt__") or []
    return approval.approval_prompt(items[0].value) if items and isinstance(items[0].value, dict) else ""


def run_turn(question: str, thread_id: str, llm_config: dict, graph=None, metadata: dict | None = None) -> dict:
    """대화 한 턴을 실행하고 Slack Delivery에 넘길 결과 dict를 반환합니다.

    - analysis : 특화 Agent 결과 전체 (dataframe·그래프 경로 포함)
    - chitchat / approval_request / approval : {"final_answer": 답변, "turn_type": ...}
    승인 대기 중이면 답변 끝에 승인 질문을 다시 붙입니다.
    """

    graph = graph or get_graph()
    turn_id = uuid.uuid4().hex
    run_metadata = {"thread_id": thread_id, "session_id": thread_id, **(metadata or {})}
    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": run_metadata,
        "run_name": "cart_signal_turn",
        "recursion_limit": 12,
    }

    if langsmith_enabled():
        print(f"[LangSmith] 추적 사용 (thread_id={thread_id})")

    # ── check_approval: 멈춰 있는 승인 요청에 대한 답이면 이어서 실행 (Command(resume=...))
    waiting = pending_interrupt(graph, thread_id)
    if waiting is not None:
        answer = approval.resolve_approval_answer(question)
        print(f"[승인 대기 중] 메시지 판단 = {answer.kind}")
        if answer.kind != "other":
            output = graph.invoke(Command(resume={"kind": answer.kind, "text": question}), config=config)
            _prune(graph, thread_id)
            return {
                "turn_type": "approval",
                "final_answer": output.get("reply", ""),
                "completed_steps": [],
                "thread_id": thread_id,
                "approval_result": output.get("approval_result") or {},
                "campaign": output.get("campaign") or {},
            }
        # 승인과 무관한 질문 → 평소처럼 처리. pending_approval이 남아 있어 끝에서 다시 승인 대기로 멈춤

    try:
        output = graph.invoke(
            {
                # 이번 메시지만 넘기면 Checkpointer가 같은 thread_id의 이전 상태와 합칩니다.
                "messages": [HumanMessage(content=question)],
                "llm_config": llm_config,
                "turn_id": turn_id,
                "user_question": question,
                "turn_type": "",
                "reply": "",
                "standalone_question": "",
                "is_followup": False,
                "conversation_reason": "",
                "was_reset": False,
                "route": "",
                "route_reason": "",
            },
            config=config,
        )
    finally:
        agent_result = pop_turn_result(turn_id)

    _prune(graph, thread_id)
    reminder = _interrupt_text(output)
    turn_type = output.get("turn_type")

    if turn_type == "analysis":
        if agent_result is None:
            raise RuntimeError("Agent 결과를 찾지 못했습니다.")
        agent_result["thread_id"] = thread_id
        agent_result["conversation_reason"] = output.get("conversation_reason", "")
        agent_result["is_followup"] = bool(output.get("is_followup"))
        agent_result["conversation_reset"] = bool(output.get("was_reset"))
        agent_result["route_reason"] = output.get("route_reason", "")
        agent_result["final_answer"] = context.add_rewritten_question_notice(
            agent_result.get("final_answer", ""), question,
            agent_result.get("standalone_question", ""), bool(output.get("is_followup")),
        )
        if reminder:
            agent_result["final_answer"] += f"\n\n{reminder}"
            agent_result["awaiting_approval"] = True
        return agent_result

    answer = output.get("reply", "")
    if reminder and turn_type != "approval_request":
        answer = f"{answer}\n\n{reminder}" if answer else reminder
    return {
        "turn_type": "approval_request" if turn_type == "approval_request" else "chitchat",
        "final_answer": answer,
        "completed_steps": [],
        "thread_id": thread_id,
        "conversation_reason": output.get("conversation_reason", ""),
        "conversation_reset": bool(output.get("was_reset")),
        "awaiting_approval": bool(reminder),
    }


def _prune(graph, thread_id: str):
    """이전 저장본 정리 (settings.PRUNE_OLD_CHECKPOINTS). 실패해도 답변은 그대로 전달합니다."""

    if settings.PRUNE_OLD_CHECKPOINTS and getattr(graph, "checkpointer", None):
        try:
            deleted = prune_thread_history(graph.checkpointer, thread_id)
            print(f"[대화 기록 정리] {thread_id}: 이전 저장본 {deleted}개 삭제")
        except Exception as exc:
            print(f"[대화 기록 정리 실패] {type(exc).__name__}: {exc}")
