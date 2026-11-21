"""Orchestrator Tool — check_approval: 사용자 승인 여부 확인 후 다음 단계 진행 (LangGraph interrupt).

흐름
    1. 승인이 필요한 결과가 생기면 state["pending_approval"]에 요청을 넣음
       (사용자 결정 저장, 또는 특화 Agent 결과의 "approval_request")
    2. manage_workflow(조건부 엣지)가 check_approval 노드로 보냄
    3. check_approval 노드가 interrupt()를 호출 → 그래프가 멈추고 Checkpointer에 저장 → Slack에 승인 질문 전달
    4. 같은 thread_id로 다음 메시지가 오면 graph.run_turn()이 멈춘 상태를 확인하고
       resolve_approval_answer()로 승인/수정/반려/취소인지 판단 → Command(resume=...)로 멈춘 지점부터 다시 실행
    5. 승인이면 update_conversation_state로 확정값 반영, 아니면 기록만 남김

승인 대기 중 받은 답의 처리 (resolve_approval_answer, 규칙 판단 · LLM 호출 없음)

    답                              처리
    ------------------------------  ---------------------------------------------------
    승인 · 네 · 좋아                 확정값 저장 (update_conversation_state)
    반려 · 아니                      저장하지 않음
    "카디건으로 수정"                수정 요청을 기록하고, 다시 확정해 달라고 안내
    취소                            승인 요청 취소
    다른 질문 ("1위가 뭐였지?")      평소처럼 답한 뒤, 승인 질문을 다시 붙이고 계속 대기
                                    (pending_approval이 남아 있어 끝에서 다시 check_approval로 멈춤)

Slack에서는 승인 질문 아래에 [승인] [반려] [승인 요청 취소] 버튼이 붙습니다 (interfaces/slack/actions.py).
버튼은 위 글자 판단을 거치지 않고 graph.run_turn(action=...)에서 승인 요청 ID를 확인한 뒤 바로 처리합니다.
이 파일은 안내 문구만 버튼 기준으로 바꿨고, 승인 판단 규칙은 그대로입니다 (2026-11-19 사용자 결정).

approval_request 형식 (특화 Agent가 결과에 넣을 때)
    {"title": "카테고리 확정", "question": "...저장할까요?", "updates": {"category": "니트"}}
"""

import re
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from agent_system.orchestrator.tools.conversation_state import describe_updates, update_conversation_state

APPROVE = re.compile(
    r"^\s*(?:승인|확정|좋아|좋아요|좋습니다|네|예|응|그래|오케이|ok|okay|yes|진행|저장)(?:[.!。~]+)?\s*$",
    re.I,
)
REJECT = re.compile(r"^\s*(반려|거절|아니|아뇨|노|no|안\s*돼|하지\s*마|저장\s*안)", re.I)
CANCEL = re.compile(r"(승인\s*)?(취소|그만|나중에)")
REVISE = re.compile(r"(수정|바꿔|변경|대신|말고|로\s*해)")
QUESTION = re.compile(r"(\?|왜|어떻게|뭐야|뭔데|알려|보여|비교|몇|얼마|분석|그래프|차트|추이)")


@dataclass
class ApprovalAnswer:
    kind: str      # approve / reject / revise / cancel / other
    text: str = ""


def resolve_approval_answer(message: str) -> ApprovalAnswer:
    """승인 대기 중 받은 메시지가 승인 답인지 판단합니다 (규칙, LLM 없음)."""

    text = str(message or "").strip()
    if not text:
        return ApprovalAnswer("other")
    if QUESTION.search(text) and not APPROVE.match(text):
        return ApprovalAnswer("other", text)
    if CANCEL.search(text) and len(text) <= 15:
        return ApprovalAnswer("cancel", text)
    if REVISE.search(text):
        return ApprovalAnswer("revise", text)
    if REJECT.match(text):
        return ApprovalAnswer("reject", text)
    if APPROVE.match(text):
        return ApprovalAnswer("approve", text)
    return ApprovalAnswer("other", text)


def build_approval_request(updates: dict, title: str = "확정값 저장", source: str = "user") -> dict:
    lines = describe_updates(updates)
    return {
        "title": title,
        "question": f"[승인 요청] 아래 내용을 기획전 확정값으로 저장할까요?\n{lines}\n→ 아래 [승인] / [반려] 버튼을 눌러 주세요. 값을 바꾸려면 채팅으로 알려 주세요 (예: \"카디건으로 수정\")",
        "updates": dict(updates),
        "source": source,
    }


def approval_prompt(request: dict) -> str:
    return str((request or {}).get("question") or "[승인 요청] 진행할까요? 아래 [승인] / [반려] 버튼을 눌러 주세요.")


def check_approval_node(state) -> dict:
    """승인 대기 노드. interrupt()에서 멈췄다가 Command(resume=...)로 다시 실행됩니다."""

    request = state.get("pending_approval") or {}
    if not request:
        return {}

    # ── 여기서 그래프가 멈춤 (Checkpointer에 저장). resume 값이 들어오면 아래로 진행
    answer = interrupt({"type": "approval", "title": request.get("title", ""), "question": approval_prompt(request)})

    answer = answer if isinstance(answer, dict) else {"kind": "approve", "text": str(answer)}
    kind, text = answer.get("kind", "other"), str(answer.get("text", ""))
    campaign = state.get("campaign") or {}
    print(f"[check_approval] {kind}: {text}")

    # 10단계의 실제 저장 직전에도 검증된 후보·카테고리·테마를 다시 확인한다.
    review_run = state.get("workflow_run") or {}
    requested = dict(request.get("updates") or {})
    if (kind == "approve" and review_run.get("workflow") == "trend_planning"
            and review_run.get("status") == "waiting_input" and review_run.get("waiting_key") == "products"
            and {"category", "products", "theme", "subtype", "colors"}.intersection(requested)):
        from agent_system.orchestrator.tools.category_approval import verify_requested_updates
        try:
            if "products" not in requested:
                raise ValueError("검증된 상품 ID가 없는 승인 요청입니다.")
            expected = verify_requested_updates(review_run, requested)
            if expected != requested:
                raise ValueError("현재 검토 결과와 승인 요청이 달라졌습니다. 다시 확정 요청하세요.")
        except ValueError as exc:
            return {"campaign": campaign, "pending_approval": {},
                    "approval_result": {"kind": "reject", "text": str(exc), "title": request.get("title", "")},
                    "turn_type": "approval", "reply": f"승인하지 않았습니다: {exc}",
                    "messages": [AIMessage(content=f"승인하지 않았습니다: {exc}", name="orchestrator")]}

    if kind == "approve" and review_run.get("workflow") == "trend_planning" and review_run.get("waiting_key") == "selling_points":
        from agent_system.orchestrator.tools.campaign_selection import updates, ids_from
        try:
            summary = review_run.get("campaign_summary") or {}
            if not summary or summary.get("ids") != ids_from(campaign) or summary.get("category") != (campaign.get("values") or {}).get("category"):
                raise ValueError("12단계 근거가 없거나 확정 상품이 변경되었습니다.")
            if updates(request.get("step13_input", ""), summary) != requested:
                raise ValueError("검증된 입력과 승인 요청이 일치하지 않습니다.")
        except (ValueError, KeyError, TypeError) as exc:
            reply = f"승인하지 않았습니다: {exc}"
            return {"campaign": campaign, "pending_approval": {}, "approval_result": {"kind": "reject", "text": str(exc)},
                    "turn_type": "approval", "reply": reply, "messages": [AIMessage(content=reply, name="orchestrator")]}
    if (kind == "approve" and review_run.get("workflow") == "trend_planning"
            and review_run.get("waiting_key") == "page"):
        from agent_system.orchestrator.tools.campaign_copy import selected_copy_value, validate_candidates
        package = review_run.get("copy_package") or {}
        chosen = next((c for c in package.get("candidates") or []
                       if c.get("number") == request.get("copy_choice") and c.get("verified")), None)
        try:
            valid = validate_candidates({**package, "status": "draft"},
                                        review_run.get("campaign_summary") or {}, campaign)
        except (ValueError, TypeError, KeyError):
            valid = None
        if (package.get("status") != "verified" or chosen is None or valid != package
                or requested.get("page") != selected_copy_value(chosen, package)):
            reply = "대표 문구 근거 또는 승인 요청이 변경되었습니다. 다시 선택해 주세요."
            return {"campaign": campaign, "pending_approval": {},
                    "approval_result": {"kind": "reject", "text": reply},
                    "turn_type": "approval", "reply": reply,
                    "messages": [AIMessage(content=reply, name="orchestrator")]}
    if kind == "approve":
        # CART_SIGNAL_CATEGORY_REVIEW_LOOP_V1: 주력 상품 승인과 함께 검토 카테고리 확정
        updates = dict(request.get("updates") or {})
        review_run = state.get("workflow_run") or {}
        if ("products" in updates and review_run.get("workflow") == "trend_planning"
                and review_run.get("review_category") and "category" not in updates):
            updates["category"] = review_run["review_category"]
        campaign = update_conversation_state(campaign, updates, source=request.get("source", "user"))
        if "selling_points" in updates and review_run.get("campaign_summary"):
            from agent_system.orchestrator.tools.campaign_copy import selection_brief
            reply = ("소구점을 확정했습니다.\n\n" + selection_brief(
                review_run["campaign_summary"], updates["selling_points"], campaign,
                review_run.get("category_market"))
                + "\n\n선택한 근거를 바탕으로 대표 문구 3안을 작성하고 검증합니다.")
        elif "page" in updates and review_run.get("copy_package"):
            reply = "대표 문구를 확정했습니다. 페이지 초안에 반영할 선택값을 저장했습니다."
        else:
            reply = f"승인했습니다. 확정값을 저장했습니다.\n{describe_updates(request.get('updates') or {})}"
    elif kind == "revise":
        reply = (f"수정 요청을 받았습니다: \"{text}\"\n"
                 "바꿀 값을 확정해 달라고 다시 말씀해 주시면 (예: \"카테고리는 카디건으로 확정\") 다시 확인받겠습니다.")
        log = list(campaign.get("log") or []) + [{"key": "_approval", "value": f"수정 요청: {text}",
                                                 "previous": request.get("title", ""), "source": "user"}]
        campaign = {**campaign, "log": log[-30:]}
    elif kind == "reject":
        reply = "반려했습니다. 확정값은 바꾸지 않았습니다."
    else:  # cancel
        reply = "승인 요청을 취소했습니다. 확정값은 바꾸지 않았습니다."

    return {
        "campaign": campaign,
        "pending_approval": {},
        "approval_result": {"kind": kind, "text": text, "title": request.get("title", ""),
                            "approved_keys": sorted(updates) if kind == "approve" else []},
        "turn_type": "approval",
        "reply": reply,
        "messages": [HumanMessage(content=text), AIMessage(content=reply, name="orchestrator")],
    }
