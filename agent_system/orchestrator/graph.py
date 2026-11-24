"""멀티턴 대화 그래프 — LangGraph StateGraph + Checkpointer + thread_id + interrupt.

    START → orchestrator ──(manage_workflow: 조건부 엣지)──▶ <특화 Agent> ──▶ check_approval | END
                                                          ├▶ workflow (워크플로 단계 실행) ──▶ check_approval | END
                                                          ├▶ check_approval (승인 대기, interrupt)
                                                          └▶ END (일상 대화)
            check_approval ──▶ workflow (선택 대기 중인 값이 승인되면 다음 단계) | END

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
- workflow_run     : 진행 중인 워크플로 run (단계·상태·지난 단계 기록, agent_system/workflows/)
- analysis_log     : 분석 결과 기록 최근 10건 (보고서 재료. 표는 runtime/report_data/에 저장)
"""

import re
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
    workflow_run: dict           # 진행 중인 워크플로 run (manage_workflow)
    analysis_log: list           # 분석 결과 기록 (보고서 재료, common_tools/report_export/analysis_log.py)


def build_graph(checkpointer=None):
    """Orchestrator 그래프를 만듭니다. checkpointer가 있으면 멀티턴 기억이 켜집니다."""

    agents = get_agents()
    builder = StateGraph(OrchestratorState)
    builder.add_node("orchestrator", orchestrator_node)
    for spec in agents:
        builder.add_node(spec.name, make_agent_node(spec))
    builder.add_node(workflow.CHECK_APPROVAL, approval.check_approval_node)
    builder.add_node(workflow.WORKFLOW_NODE, workflow.run_workflow_node)

    builder.add_edge(START, "orchestrator")
    # manage_workflow: 조건부 엣지
    builder.add_conditional_edges(
        "orchestrator", workflow.after_orchestrator,
        [spec.name for spec in agents] + [workflow.WORKFLOW_NODE, workflow.CHECK_APPROVAL, END],
    )
    for spec in agents:
        builder.add_conditional_edges(spec.name, workflow.after_agent, [workflow.CHECK_APPROVAL, END])
    builder.add_conditional_edges(workflow.WORKFLOW_NODE, workflow.after_workflow, [workflow.CHECK_APPROVAL, END])
    builder.add_conditional_edges(workflow.CHECK_APPROVAL, workflow.after_approval, [workflow.WORKFLOW_NODE, END])
    return builder.compile(checkpointer=checkpointer)


_graph = None


def get_graph():
    """SQLite 기억이 연결된 Orchestrator 그래프 (프로세스당 1개)."""

    global _graph
    if _graph is None:
        _graph = build_graph(get_checkpointer())
    return _graph


def _snapshot(graph, thread_id: str):
    try:
        return graph.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return None


def _interrupts(snapshot) -> list:
    if snapshot is None:
        return []
    interrupts = list(getattr(snapshot, "interrupts", ()) or ())
    if not interrupts:
        for task in getattr(snapshot, "tasks", ()) or ():
            interrupts.extend(getattr(task, "interrupts", ()) or ())
    return interrupts


def pending_interrupt(graph, thread_id: str):
    """thread가 check_approval에서 멈춰 있으면 interrupt 값을, 아니면 None."""

    interrupts = _interrupts(_snapshot(graph, thread_id))
    return interrupts[0].value if interrupts else None


def pending_interrupt_token(graph, thread_id: str) -> str:
    """멈춰 있는 승인 요청의 고유 ID (Slack 버튼이 '그 요청'에 대한 것인지 확인용). 없으면 빈 문자열."""

    interrupts = _interrupts(_snapshot(graph, thread_id))
    return str(getattr(interrupts[0], "id", "") or "") if interrupts else ""


# ---------------------------------------------------------------- Slack 버튼으로 받을 수 있는 확인
ACTIVE_WORKFLOW_STATUS = ("blocked", "waiting_input")


def pending_actions(graph, thread_id: str) -> list[dict]:
    """지금 사용자 확인을 기다리는 것 (Slack이 버튼으로 보여 줌, interfaces/slack/actions.py).

    - 승인 대기(check_approval interrupt) → [{"type": "approval", "token", "title"}]   (승인·반려·취소)
    - 승인 대기가 없고 워크플로가 멈춤/선택 대기 → [{"type": "workflow", "run_id", "status"}]
      (멈춤: 다시 시도·진행 상황·취소 / 선택 대기: 진행 상황·취소)
    승인 대기가 있으면 워크플로 버튼은 보여 주지 않습니다 (한 번에 한 가지 확인만).
    """

    snapshot = _snapshot(graph, thread_id)
    interrupts = _interrupts(snapshot)
    if interrupts:
        value = interrupts[0].value if isinstance(interrupts[0].value, dict) else {}
        return [{"type": "approval", "token": str(getattr(interrupts[0], "id", "") or ""),
                 "title": str(value.get("title", ""))}]
    run = ((getattr(snapshot, "values", None) or {}) if snapshot is not None else {}).get("workflow_run") or {}
    preview = run.get('page_preview') or {}
    if (run.get('status') == 'waiting_input' and run.get('waiting_key') == 'page'
            and preview.get('status') == 'review' and run.get('run_id')):
        return [{'type': 'page_preview_review', 'run_id': run['run_id'],
                 'revision': int(preview.get('button_epoch') or preview.get('revision') or 1)}]
    if (run.get('status') == 'waiting_input' and run.get('waiting_key') == 'page'
            and preview.get('status') == 'rejected' and run.get('run_id')):
        return []
    if (run.get('status') == 'waiting_input' and run.get('waiting_key') == 'page'
            and (run.get('copy_package') or {}).get('status') == 'verified' and run.get('run_id')):
        return [{'type': 'copy_revision_review', 'run_id': run['run_id'],
                 'revision': int(run['copy_package'].get('revision') or 0),
                 'phase': 'review'}]
    target = run.get('campaign_target_r39') or {}
    if (preview.get('status') == 'approved' and run.get('status') == 'paused' and target.get('status') == 'review'
            and target.get('proposal') and target.get('review_token') and run.get('run_id')):
        return [{'type':'target_selection_review', 'run_id':run['run_id'], 'token':target['review_token'],
                 **({'fixed_policy':True} if target.get('policy') == 'r40_v1' else {})}]
    package41 = run.get('segment_copy_r41') or {}
    if run.get('status') == 'paused' and target.get('status') == 'confirmed' and target.get('policy') == 'r40_v1' and package41.get('status') == 'review' and package41.get('token'):
        return [{'type':'segment_copy_review','run_id':run['run_id'],'token':package41['token']}]
    # 승인 후에는 일반 워크플로 실행 버튼 대신 명시적 업무 요청을 기다린다.
    if preview.get('status') == 'approved' and (run.get('target_start_pending') or run.get('campaign_target_r39')):
        return []
    draft = run.get('report_draft') or {}
    if (run.get('run_id') and draft.get('status') == 'review'
            and run.get('status') == 'paused'):
        return [{'type':'report_draft_review', 'run_id':run['run_id'],
                 'revision':int(draft.get('revision') or 1)}]
    if run.get("status") == "waiting_input" and run.get("run_id"):
        if run.get("waiting_key") == "category_decision" and run.get("review_category"):
            return [{"type": "category_confirm", "run_id": run["run_id"],
                     "category": run["review_category"],
                     **({"token": run["category_decision_token"]} if run.get("category_decision_token") else {})}]
        # 세부 유형·색상은 반드시 마케터의 자연어 입력으로 선택한다.
    if run.get("status") in ACTIVE_WORKFLOW_STATUS and run.get("run_id"):
        return [{"type": "workflow", "run_id": run["run_id"], "status": run["status"]}]
    return []


def _interrupt_text(output: dict) -> str:
    items = output.get("__interrupt__") or []
    return approval.approval_prompt(items[0].value) if items and isinstance(items[0].value, dict) else ""


# ---------------------------------------------------------------- 승인 입력 안내 (A안)
# 승인 판단 규칙(approval.APPROVE)은 보안상 "승인", "네", "좋아요"처럼 한 단어 전체 일치만 승인으로 받습니다.
# 그보다 긴 승인 표현은 승인으로 처리하지 않고, 아래 안내만 보여 줍니다 (질문은 평소처럼 처리).
APPROVAL_INPUT_GUIDE = "승인 대기 중입니다. 아래 [승인] / [반려] 버튼을 눌러 주세요."
_APPROVAL_LIKE_WORDS = {
    "승인", "승인합니다", "승인할게", "승인할게요", "승인해", "승인해요", "승인해줘", "승인해주세요", "승인이요",
    "확정", "확정해", "확정해요", "확정해줘", "확정해주세요", "확정할게", "확정할게요", "확정합니다",
    "저장", "저장해", "저장해요", "저장해줘", "저장해주세요", "저장할게요", "저장합니다",
    "진행", "진행해", "진행해요", "진행해줘", "진행해주세요", "진행할게요", "진행합니다", "진행하자",
    "네", "넵", "네네", "예", "응", "응응", "ㅇㅇ", "그래", "그래요", "오케이", "ok", "okay", "yes",
    "좋아", "좋아요", "좋습니다", "좋네요", "맞아", "맞아요", "맞습니다",
    "그걸로", "그대로", "그렇게", "해", "해줘", "해주세요", "할게요", "부탁해요", "부탁드려요", "부탁합니다",
}


def looks_like_approval_intent(message: str) -> bool:
    """승인 대기 중, 질문이 아니고 승인·동의 표현으로만 이루어진 짧은 답인지 (안내용, 승인 처리 아님)."""
    text = str(message or "").strip()
    if not text or len(text) > 30 or approval.QUESTION.search(text):
        return False
    words = [w.strip(".,!~。·") for w in re.split(r"[\s,]+", text.lower())]
    words = [w for w in words if w]
    return bool(words) and all(w in _APPROVAL_LIKE_WORDS for w in words)


APPROVAL_BUTTON_LABELS = {"approve": "승인", "reject": "반려", "cancel": "승인 요청 취소"}
WORKFLOW_BUTTON_COMMANDS = {"continue": "워크플로 계속", "status": "워크플로 상태", "cancel": "워크플로 취소"}
STALE_ACTION_REPLY = "이 버튼은 이미 처리되었거나 지난 요청의 버튼입니다. 가장 최근 메시지의 버튼을 눌러 주세요."



# CART_SIGNAL_MARKET_CHART_BRIDGE_V1
def _attach_market_trend_chart(result: dict, question: str) -> dict:
    """시장 추이 DataFrame이 있을 때 기존 공통 선그래프를 생성합니다."""
    import re
    from pathlib import Path
    import pandas as pd

    # 사용자가 명시적으로 그래프를 요청했을 때만 실행
    if not re.search(r"그래프|차트|시각화", question, re.I):
        return result
    if re.search(r"그래프\\s*(?:없이|제외|말고)|차트\\s*(?:없이|제외|말고)", question):
        return result

    frame = result.get("dataframe")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return result
    if "period" not in frame.columns:
        return result
    if result.get("visualization_path"):
        return result

    # 현재 네이버 추이 표: period + 키워드별 숫자 열
    numeric_columns = [
        col for col in frame.columns
        if col != "period"
        and pd.api.types.is_numeric_dtype(frame[col])
    ]
    if len(numeric_columns) != 1:
        return result

    y_column = numeric_columns[0]

    try:
        from agent_system.tools.visualization_tools import create_line_chart

        chart = create_line_chart(
            df=frame,
            x_column="period",
            y_column=y_column,
            title=f"{y_column} 관심 추이",
            x_label="관측 기간",
            y_label="상대 관심도 (검색 횟수 아님)",
        )

        chart_path = chart.get("file_path")
        if not chart.get("success") or not chart_path:
            raise ValueError(chart.get("error") or "선그래프 생성 실패")
        if not Path(chart_path).is_file():
            raise FileNotFoundError(chart_path)

        result["visualization_path"] = str(chart_path)
        result["visualization_result"] = chart
        steps = list(result.get("completed_steps") or [])
        if "visualization" not in steps:
            steps.append("visualization")
        result["completed_steps"] = steps

        print(f"[시장 추이 그래프 생성] {chart_path}", flush=True)

    except Exception as exc:
        # 분석 결과를 보존하되 그래프 실패 사실을 명확히 안내
        print(
            f"[시장 추이 그래프 실패] {type(exc).__name__}: {exc}",
            flush=True,
        )
        result["final_answer"] = (
            str(result.get("final_answer") or "")
            + "\\n\\n[시각화 미완료] 분석 데이터는 생성됐지만 "
              "그래프 파일 생성에 실패했습니다."
        )
        result["visualization_error"] = str(exc)

    return result


def run_turn(question: str, thread_id: str, llm_config: dict, graph=None, metadata: dict | None = None,
             action: dict | None = None) -> dict:
    """대화 한 턴을 실행하고 Slack Delivery에 넘길 결과 dict를 반환합니다.

    - analysis : 특화 Agent 결과 전체 (dataframe·그래프 경로 포함)
    - chitchat / approval_request / approval : {"final_answer": 답변, "turn_type": ...}
    승인 대기 중이면 답변 끝에 승인 질문을 다시 붙입니다.

    action (Slack 버튼, 선택)
        {"type": "approval", "decision": "approve"|"reject"|"cancel", "token": 승인 요청 ID}
        {"type": "workflow", "command": "continue"|"status"|"cancel", "run_id": 워크플로 run ID}
        글자 판단 없이 버튼 값 그대로 처리합니다. 버튼이 가리키는 요청이 지금 기다리는 요청과 다르면 처리하지 않습니다.
    결과의 "actions"에는 지금 사용자 확인을 기다리는 것(pending_actions)을 담아 Slack이 버튼을 붙입니다.
    """

    graph = graph or get_graph()
    # CART_SIGNAL_CATEGORY_CONTEXT_V1: 직전 카테고리 비교는 H&M 상품 검색으로 라우팅하지 않음
    if action is None:
        from agent_system.orchestrator.tools.category_comparison import (
            is_category_followup, build_category_comparison,
        )
        if is_category_followup(question):
            snapshot = _snapshot(graph, thread_id)
            values = getattr(snapshot, "values", {}) or {}
            # 현재 스레드의 이전 AI 답변만 확인. 다른 세션 내용은 섞지 않는다.
            from langchain_core.messages import AIMessage
            messages = values.get("messages") or []
            previous_answers = "\n".join(
                str(m.content) for m in messages[-10:] if isinstance(m, AIMessage)
            )
            comparison = build_category_comparison(question, previous_answers)
            comparison.update(turn_type="analysis", thread_id=thread_id,
                              route="market_analysis", standalone_question=question)
            comparison["actions"] = []
            print("[카테고리 비교] 네이버 근거 기반 응답; H&M 상품 분석 호출하지 않음", flush=True)
            return comparison
    result = _run_turn(question, thread_id, llm_config, graph, metadata, action)
    # 대분류 확정 턴은 워크플로 실행이 아닌 대화 응답으로 종료하므로,
    # 저장된 분석 증거에서 표·그래프를 이 시점에 별도 첨부한다.
    from agent_system.orchestrator.tools.category_planning.confirmation_report import enrich_confirmation_result
    if result.get("turn_type") == "chitchat":
        from agent_system.orchestrator.tools.category_planning.confirmation_report import is_subtype_replay_request
        approval_turn = (action or {}).get("type") == "category_confirm"
        replay_turn = is_subtype_replay_request(question)
        # 유형을 선택한 직후는 색상 증거만 추가한다. 승인을 다시 요청하지 않는다.
        # 상세 분석은 후보를 잠깐 표시할 뿐, 선택·확정 상태를 변경하지 않는다.
        from agent_system.orchestrator.tools.category_planning.exploration_r28 import requested_subtype_review
        snapshot = _snapshot(graph, thread_id)
        run = ((getattr(snapshot, "values", None) or {}) if snapshot is not None else {}).get("workflow_run") or {}
        preview = requested_subtype_review(question, run.get('subtype_options') or []) if (
            run.get('status') == 'waiting_input' and run.get('waiting_key') == 'subtype_colors'
            and run.get('category_confirmed')) else None
        if preview and run.get('preview_subtype') == preview:
            display_run = {**run, 'selected_subtype': preview, 'selected_colors': []}
            result = enrich_confirmation_result(result, display_run, mode='colors')
        elif approval_turn or replay_turn:
            result = enrich_confirmation_result(result, run, mode='types')
    actions = pending_actions(graph, thread_id)
    # 워크플로 버튼은 워크플로 결과·워크플로 명령 답변에만 붙임 (대기 중 다른 질문마다 버튼이 쌓이지 않게)
    if actions and actions[0]['type'] == 'target_selection_review' and '고객 타깃 선정 제안' not in str(result.get('final_answer','')):
        actions = []
    if actions and actions[0]['type'] == 'segment_copy_review' and '고객군별 마케팅 문구 ·' not in str(result.get('final_answer','')):
        actions = []
    if actions and actions[0]['type'] == 'page_preview_review':
        if (action or {}).get('type') != 'page_preview_review' and not (
                result.get('turn_type') == 'chitchat'
                and ('페이지 틀' in str(result.get('final_answer','')) or '페이지 목업' in str(result.get('final_answer','')))):
            actions = []
    if actions and actions[0]['type'] == 'copy_revision_review':
        if (action or {}).get('type') != 'copy_revision_review' and not (
                result.get('turn_type') == 'chitchat'
                and ('다시 작성했습니다' in str(result.get('final_answer',''))
                     or '카피 수정' in str(result.get('final_answer','')))):
            actions = []
    if actions and actions[0]["type"] == "workflow":
        workflow_turn = (result.get("turn_type") == "workflow" or (action or {}).get("type") == "workflow"
                         or workflow.is_workflow_command(question)
                         or any(label in str(result.get("final_answer", "")) for label in workflow.BUTTON_LABELS))
        if not workflow_turn:
            actions = []
    # R42.2: 실제 R40 제안의 집계만 PNG로 전달 (실패해도 텍스트·승인 유지).
    if actions and actions[0].get('type') == 'target_selection_review' and actions[0].get('fixed_policy'):
        from agent_system.orchestrator.tools.target_presentation_r422 import optional_chart
        snapshot = _snapshot(graph, thread_id)
        display_run = (getattr(snapshot, 'values', None) or {}).get('workflow_run') or {}
        target = display_run.get('campaign_target_r39') or {}
        if target.get('review_token') == actions[0].get('token'):
            path = optional_chart(target, display_run)
            if path:
                # 비공개 집계 PNG는 허용된 프로젝트 첨부 경로로 전달한다.
                result['attachments'] = [*(result.get('attachments') or []), path]
    result["actions"] = actions
    return result


def _resume_approval(graph, config: dict, turn_id: str, thread_id: str, kind: str, text: str) -> dict:
    """멈춰 있는 승인 요청에 답을 넣어 이어서 실행 (Command(resume=...))."""

    # turn_id를 새로 넣어 승인 뒤 워크플로가 이어서 실행되면 그 결과를 이번 턴으로 받습니다
    try:
        output = graph.invoke(Command(resume={"kind": kind, "text": text}, update={"turn_id": turn_id}), config=config)
    finally:
        resumed_result = pop_turn_result(turn_id)
    _prune(graph, thread_id)
    if output.get("turn_type") == "workflow" and resumed_result is not None:
        resumed_result.update(thread_id=thread_id, approval_result=output.get("approval_result") or {},
                              campaign=output.get("campaign") or {})
        return _with_reminders(resumed_result, output)
    return {
        "turn_type": "approval",
        "final_answer": output.get("reply", ""),
        "completed_steps": [],
        "thread_id": thread_id,
        "approval_result": output.get("approval_result") or {},
        "campaign": output.get("campaign") or {},
    }


def _notice(thread_id: str, text: str) -> dict:
    return {"turn_type": "approval", "final_answer": text, "completed_steps": [], "thread_id": thread_id}


def _run_turn(question: str, thread_id: str, llm_config: dict, graph, metadata: dict | None,
              action: dict | None) -> dict:
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

    # ── Slack 버튼 (글자 판단 없이 버튼 값 그대로)
    skip_approval_check = False
    if not action and str(question).strip().startswith(('__cart_target_review__:', '__cart_segment_copy__:')):
        return _notice(thread_id, STALE_ACTION_REPLY)
    if action:
        kind = action.get("type")
        if kind == "approval":
            decision = action.get("decision")
            token = pending_interrupt_token(graph, thread_id)
            if decision not in APPROVAL_BUTTON_LABELS or pending_interrupt(graph, thread_id) is None \
                    or not token or token != str(action.get("token") or ""):
                print(f"[버튼] 승인 요청 불일치·없음 → 처리 안 함 ({decision})")
                return _notice(thread_id, STALE_ACTION_REPLY)
            print(f"[버튼] 승인 요청 → {decision}")
            return _resume_approval(graph, config, turn_id, thread_id, decision,
                                    f"[버튼] {APPROVAL_BUTTON_LABELS[decision]}")
        if kind == 'target_selection_review':
            from agent_system.orchestrator.tools.campaign_target_r39 import review_action_valid
            snapshot = _snapshot(graph, thread_id)
            run = (getattr(snapshot, 'values', None) or {}).get('workflow_run') or {}
            if pending_interrupt(graph, thread_id) is not None or not review_action_valid(run, action):
                return _notice(thread_id, STALE_ACTION_REPLY)
            question = '__cart_target_review__:' + action['command']
            skip_approval_check = True
        elif kind == 'segment_copy_review':
            from agent_system.orchestrator.tools.campaign_copy_r41 import action_valid
            snapshot = _snapshot(graph, thread_id)
            run = (getattr(snapshot, 'values', None) or {}).get('workflow_run') or {}
            if pending_interrupt(graph, thread_id) is not None or not action_valid(run, action):
                return _notice(thread_id, STALE_ACTION_REPLY)
            question = '__cart_segment_copy__:' + action['command']
            skip_approval_check = True
        elif kind == 'page_preview_review':
            snapshot = _snapshot(graph, thread_id)
            run = ((getattr(snapshot, 'values', None) or {}) if snapshot is not None else {}).get('workflow_run') or {}
            preview = run.get('page_preview') or {}
            if (run.get('status') != 'waiting_input' or run.get('waiting_key') != 'page'
                    or run.get('run_id') != action.get('run_id')
                    or int(preview.get('button_epoch') or preview.get('revision') or 0) != action.get('revision')
                    or preview.get('status') != 'review'):
                return _notice(thread_id, STALE_ACTION_REPLY)
            command = action.get('command')
            if command not in ('approve','reject','edit','regenerate','undo'):
                return _notice(thread_id, STALE_ACTION_REPLY)
            question = '__cart_page_preview__:' + command
            skip_approval_check = True
        elif kind == 'copy_revision_review':
            snapshot = _snapshot(graph, thread_id)
            run = ((getattr(snapshot, 'values', None) or {}) if snapshot is not None else {}).get('workflow_run') or {}
            if (run.get('status') != 'waiting_input' or run.get('waiting_key') != 'page'
                    or run.get('run_id') != action.get('run_id')
                    or action.get('phase') != 'review'
                    or int((run.get('copy_package') or {}).get('revision') or 0) != action.get('revision')):
                return _notice(thread_id, STALE_ACTION_REPLY)
            command = action.get('command')
            if command not in ('edit','undo','choose1','choose2','choose3'):
                return _notice(thread_id, STALE_ACTION_REPLY)
            question = '__cart_copy_review__:' + command
            skip_approval_check = True
        elif kind == 'report_draft_review':
            snapshot = _snapshot(graph, thread_id)
            run = ((getattr(snapshot, 'values', None) or {}) if snapshot is not None else {}).get('workflow_run') or {}
            draft = run.get('report_draft') or {}
            if (run.get('run_id') != action.get('run_id')
                    or draft.get('status') != 'review'
                    or int(draft.get('revision') or 0) != action.get('revision')):
                return _notice(thread_id, STALE_ACTION_REPLY)
            if action.get('command') not in ('approve','edit','cancel'):
                return _notice(thread_id, STALE_ACTION_REPLY)
            question = '__cart_report_review__:' + action['command']
            skip_approval_check = True
        elif kind in {"category_confirm", "subtype_suggestion"}:
            # Slack 버튼은 현재 Run·대기 키·추천 후보와 완전히 일치할 때만 허용한다.
            snapshot = _snapshot(graph, thread_id)
            run = ((getattr(snapshot, "values", None) or {}) if snapshot is not None else {}).get("workflow_run") or {}
            if run.get("status") != "waiting_input" or not run.get("run_id") or run["run_id"] != action.get("run_id"):
                return _notice(thread_id, STALE_ACTION_REPLY)
            if kind == "category_confirm":
                if (run.get("waiting_key") != "category_decision" or
                        run.get("review_category") != action.get("category") or
                        (run.get("category_decision_token") or "") != (action.get("token") or "")):
                    return _notice(thread_id, STALE_ACTION_REPLY)
                decision = action.get("decision", "approve")
                if decision not in {"approve", "reject", "cancel"}:
                    return _notice(thread_id, STALE_ACTION_REPLY)
                question = (f"{run['review_category']} 확정 승인" if decision == "approve"
                            else f"{run['review_category']} 대분류 승인 요청 {decision}")
            else:
                options = run.get("subtype_options") or []
                if run.get("waiting_key") != "subtype_colors" or not options:
                    return _notice(thread_id, STALE_ACTION_REPLY)
                top = options[0]
                colors = [c["name"] for c in top.get("colors") or []][:2]
                if top.get("keyword") != action.get("subtype") or colors != action.get("colors") or not colors:
                    return _notice(thread_id, STALE_ACTION_REPLY)
                question = "유형: 1, 색상: " + ", ".join(colors)
            skip_approval_check = True
        elif kind == "workflow":
            command = action.get("command")
            snapshot = _snapshot(graph, thread_id)
            run = ((getattr(snapshot, "values", None) or {}) if snapshot is not None else {}).get("workflow_run") or {}
            if command not in WORKFLOW_BUTTON_COMMANDS or not run.get("run_id") \
                    or run.get("run_id") != action.get("run_id") or run.get("status") not in ACTIVE_WORKFLOW_STATUS:
                print(f"[버튼] 워크플로 불일치·종료 → 처리 안 함 ({command})")
                return _notice(thread_id, STALE_ACTION_REPLY)
            print(f"[버튼] 워크플로 → {command}")
            question = WORKFLOW_BUTTON_COMMANDS[command]   # 기존 워크플로 명령과 같은 처리
            skip_approval_check = True                     # 승인 답으로 해석하지 않음
        else:
            return _notice(thread_id, "알 수 없는 버튼입니다.")

    # ── check_approval: 멈춰 있는 승인 요청에 대한 답이면 이어서 실행 (Command(resume=...))
    waiting = None if skip_approval_check else pending_interrupt(graph, thread_id)
    if waiting is not None:
        answer = approval.resolve_approval_answer(question)
        print(f"[승인 대기 중] 메시지 판단 = {answer.kind}")
        if answer.kind != "other":
            return _resume_approval(graph, config, turn_id, thread_id, answer.kind, question)
        if looks_like_approval_intent(question):
            # 승인 의도는 보이지만 승인 규칙(한 단어 전체 일치)과 다른 답 (예: "승인합니다", "네 좋아요")
            # → LLM에 넘기지 않고 입력 방법만 안내. 승인 판단 규칙(approval.py)은 바꾸지 않으며 계속 승인 대기
            print("[승인 대기 중] 승인 의도로 보이는 답 → 입력 방법 안내")
            return {
                "turn_type": "approval_request",
                "final_answer": f"{APPROVAL_INPUT_GUIDE}\n\n{approval.approval_prompt(waiting)}",
                "completed_steps": [],
                "thread_id": thread_id,
                "awaiting_approval": True,
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

    if turn_type == "report":
        # 보고서 요청: 답변 + PDF·Word 첨부 (Slack Delivery가 attachments를 업로드)
        result = agent_result or {"final_answer": output.get("reply", ""), "attachments": []}
        result.update(turn_type="report", thread_id=thread_id, completed_steps=[])
        return _with_reminders(result, output)

    if turn_type == "workflow":
        if agent_result is None:
            raise RuntimeError("워크플로 결과를 찾지 못했습니다.")
        agent_result["thread_id"] = thread_id
        agent_result["conversation_reset"] = bool(output.get("was_reset"))
        return _with_reminders(agent_result, output)

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
        if output.get("route") == "market_analysis":
            agent_result = _attach_market_trend_chart(
                agent_result,
                agent_result.get("standalone_question") or question,
            )
        # "그래프로 보여줘"처럼 그래프만 요청했고 그래프가 만들어졌으면 Slack에는 제목 + 그래프만 보냄
        if (context.is_chart_only_request(question, bool(output.get("is_followup")))
                and agent_result.get("visualization_path") and not agent_result.get("error")):
            agent_result["full_answer"] = agent_result["final_answer"]
            agent_result["final_answer"] = context.chart_only_answer(agent_result["final_answer"])
            agent_result["delivery_mode"] = "chart_only"
        return _with_reminders(agent_result, output)

    # R29: 읽기 전용 소구점 재표시는 그래프 분기를 실행하지 않고 파일 포함 결과를 전송한다.
    if agent_result is not None and agent_result.get('replay_delivery'):
        agent_result['thread_id'] = thread_id
        return _with_reminders(agent_result, output)

    answer = output.get("reply", "")
    if reminder and turn_type != "approval_request":
        answer = f"{answer}\n\n{reminder}" if answer else reminder
    # 워크플로가 마케터 선택을 기다리는 중이면 다른 질문에 답한 뒤 현재 단계를 다시 안내 (같은 단계로 복귀)
    step_note = workflow.workflow_reminder(output.get("workflow_run"), output.get("campaign"))
    if step_note and turn_type != "approval_request":
        answer = f"{answer}\n\n{step_note}" if answer else step_note
    result = {
        "turn_type": "approval_request" if turn_type == "approval_request" else "chitchat",
        "final_answer": answer,
        "completed_steps": [],
        "thread_id": thread_id,
        "conversation_reason": output.get("conversation_reason", ""),
        "conversation_reset": bool(output.get("was_reset")),
        "awaiting_approval": bool(reminder),
    }
    for key in ("visualization_path", "visualization_result", "attachments",
                "page_preview_path", "page_preview_revision"):
        if key in output:
            result[key] = output[key]
    return result


def _with_reminders(result: dict, output: dict) -> dict:
    """Agent·워크플로 결과 끝에 승인 대기·워크플로 단계 안내를 붙입니다."""

    reminder = _interrupt_text(output)
    if reminder:
        result["final_answer"] = f"{result.get('final_answer', '')}\n\n{reminder}"
        result["awaiting_approval"] = True
    if result.get("turn_type") != "workflow":
        step_note = workflow.workflow_reminder(output.get("workflow_run"), output.get("campaign"))
        if step_note:
            result["final_answer"] = f"{result.get('final_answer', '')}\n\n{step_note}"
    return result


def _prune(graph, thread_id: str):
    """이전 저장본 정리 (settings.PRUNE_OLD_CHECKPOINTS). 실패해도 답변은 그대로 전달합니다."""

    if settings.PRUNE_OLD_CHECKPOINTS and getattr(graph, "checkpointer", None):
        try:
            deleted = prune_thread_history(graph.checkpointer, thread_id)
            print(f"[대화 기록 정리] {thread_id}: 이전 저장본 {deleted}개 삭제")
        except Exception as exc:
            print(f"[대화 기록 정리 실패] {type(exc).__name__}: {exc}")
