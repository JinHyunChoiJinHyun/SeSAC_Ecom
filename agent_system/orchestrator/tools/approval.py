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

approval_request 형식 (특화 Agent가 결과에 넣을 때)
    {"title": "카테고리 확정", "question": "...저장할까요?", "updates": {"category": "니트"}}
"""

import re
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from agent_system.orchestrator.tools.conversation_state import describe_updates, update_conversation_state

APPROVE = re.compile(r"^\s*(승인|확정|좋아|좋습니다|네|예|응|그래|오케이|ok|okay|yes|진행|저장)", re.I)
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
        "question": f"[승인 요청] 아래 내용을 기획전 확정값으로 저장할까요?\n{lines}\n→ '승인' / '반려' / 수정 내용(예: \"카디건으로 수정\")",
        "updates": dict(updates),
        "source": source,
    }


def approval_prompt(request: dict) -> str:
    return str((request or {}).get("question") or "[승인 요청] 진행할까요? ('승인' / '반려')")


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

    if kind == "approve":
        campaign = update_conversation_state(campaign, request.get("updates") or {}, source=request.get("source", "user"))
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
        "approval_result": {"kind": kind, "text": text, "title": request.get("title", "")},
        "turn_type": "approval",
        "reply": reply,
        "messages": [HumanMessage(content=text), AIMessage(content=reply, name="orchestrator")],
    }
