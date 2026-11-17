"""Orchestrator 5개 기능 테스트 (LLM 대신 고정 응답, OpenAI 키·DB 불필요).

    route_request · manage_context · update_conversation_state · check_approval(interrupt) · manage_workflow

대화 기억은 임시 SQLite 파일(SqliteSaver)을 써서 '봇 재시작 후 승인 이어 받기'까지 확인합니다.
실행: python tests/orchestrator_flow_test.py
"""

import dataclasses
import os
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path


def _find_project_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents, Path.cwd()]:
        if (candidate / "agent_system").is_dir():
            return candidate
    sys.exit("agent_system 폴더를 찾지 못했습니다. 프로젝트 경로를 인자로 주세요.")


PROJECT = _find_project_root()
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)
print(f"[Orchestrator 흐름 테스트] 프로젝트 경로: {PROJECT}")

import pandas as pd  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

from interfaces.slack.delivery import deliver_to_slack  # noqa: E402
from agent_system.orchestrator import registry, settings  # noqa: E402
from agent_system.orchestrator.graph import build_graph, pending_interrupt, run_turn  # noqa: E402
from agent_system.orchestrator.tools import approval, context, intent, workflow  # noqa: E402
from agent_system.orchestrator.tools.intent import IntentDecision, StateUpdate  # noqa: E402
from agent_system.orchestrator.tools.conversation_state import update_conversation_state  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'} | {name}" + (f" | {detail}" if detail and not condition else ""))


# ------------------------------------------------------------------ stub
class IntentStub:
    def __init__(self):
        self.plan, self.inputs = None, []

    def invoke(self, payload, *args, **kwargs):
        self.inputs.append(payload["messages"][-1].content)
        return {"structured_response": self.plan}


intent_stub = IntentStub()
intent.get_intent_agent = lambda cfg=None: intent_stub

AGENT_CALLS = []
AGENT_EXTRA = {}


def general_stub(request):
    AGENT_CALLS.append(request)
    return {"final_answer": f"분석 결과: {request.question}",
            "dataframe": pd.DataFrame({"product_type_name": ["Sweater", "Trousers"], "purchase_count": [10, 5]}),
            "sql_query": "SELECT 1", "data_sources": ["hm"], **AGENT_EXTRA}


registry.AGENTS[0] = dataclasses.replace(registry.AGENTS[0], runner="__main__:general_stub")
LLM = {"provider": "stub", "model": "stub"}


def plan(intent_name, **kwargs):
    intent_stub.plan = IntentDecision(intent=intent_name, reason="stub", **kwargs)


db_path = Path(tempfile.mkdtemp(prefix="cs_flow_")) / "memory.sqlite"


def new_graph():
    return build_graph(SqliteSaver(sqlite3.connect(str(db_path), check_same_thread=False)))


graph = new_graph()
T = "dm:U_FLOW"


def values(thread=T, g=None):
    return (g or graph).get_state({"configurable": {"thread_id": thread}}).values


# ================================================================ 구조
nodes = set(graph.get_graph().nodes)
check("그래프: orchestrator · general_analysis · check_approval 노드", {"orchestrator", "general_analysis", "check_approval"} <= nodes)
edges = {(e.source, e.target, e.conditional) for e in graph.get_graph().edges}
check("manage_workflow: orchestrator·Agent 뒤가 조건부 엣지",
      ("orchestrator", "check_approval", True) in edges and ("general_analysis", "check_approval", True) in edges)

# ================================================================ resolve_approval_answer
r = approval.resolve_approval_answer
check("승인 답 판단: 승인/네/좋아", all(r(t).kind == "approve" for t in ("승인", "네 좋아요", "좋아 진행해")))
check("승인 답 판단: 반려/아니", all(r(t).kind == "reject" for t in ("반려", "아니요")))
check("승인 답 판단: 수정", r("카디건으로 수정해줘").kind == "revise")
check("승인 답 판단: 취소", r("취소").kind == "cancel")
check("승인 답 판단: 다른 질문은 other", all(r(t).kind == "other" for t in ("남성 판매는?", "월별 추이 그래프 보여줘")))

# ================================================================ route_request · analysis
plan("analysis", standalone_question="H&M 여성 상품 유형 Top 10", is_followup=False)
res = run_turn("여성 상품 유형 Top 10", T, LLM, graph=graph)
check("route_request: 분석 요청 → general_analysis", res["turn_type"] == "analysis" and res["route"] == "general_analysis")
check("분석 턴: 승인 대기 없음", pending_interrupt(graph, T) is None and not res.get("awaiting_approval"))

# ================================================================ decision → check_approval (interrupt)
plan("decision", updates=[StateUpdate(key="category", value="니트")])
res = run_turn("카테고리는 니트로 확정할게", T, LLM, graph=graph)
check("decision: 승인 질문 전송", res["turn_type"] == "approval_request" and "[승인 요청]" in res["final_answer"]
      and "니트" in res["final_answer"])
check("check_approval: interrupt로 그래프 멈춤", (pending_interrupt(graph, T) or {}).get("type") == "approval")
check("승인 전에는 확정값 저장 안 함", not (values().get("campaign") or {}).get("values"))

# 승인 대기 중 다른 질문 → 답하고 다시 승인 대기
plan("chitchat", reply="1위는 Sweater입니다.")
res = run_turn("1위가 뭐였지?", T, LLM, graph=graph)
check("승인 대기 중 다른 질문: 답변 + 승인 질문 다시 붙임", "Sweater" in res["final_answer"]
      and "[승인 요청]" in res["final_answer"] and res.get("awaiting_approval"))
check("승인 대기 중 다른 질문: 여전히 멈춤", pending_interrupt(graph, T) is not None)
check("manage_context: 대화 판단 입력에 승인 대기 표시", "[승인 대기 중]" in intent_stub.inputs[-1])

# 봇 재시작 후 승인
graph = new_graph()
calls_before = len(intent_stub.inputs)
res = run_turn("승인", T, LLM, graph=graph)
check("재시작 후에도 승인 이어 받기 (Command resume)", res["turn_type"] == "approval"
      and "저장했습니다" in res["final_answer"])
check("승인 답은 LLM 호출 없이 처리", len(intent_stub.inputs) == calls_before)
v = values()
check("update_conversation_state: category=니트 저장", v["campaign"]["values"].get("category") == "니트"
      and v["campaign"]["stage"] == "category_confirmed")
check("변경 기록(log) 남김", v["campaign"]["log"][-1]["key"] == "category")
check("승인 후 대기 해제", pending_interrupt(graph, T) is None and not v.get("pending_approval"))
check("승인 답·결과가 대화 기록에 남음", any("승인" == str(m.content) for m in v["messages"]))

# manage_context: 다음 질문에 확정값 참조
plan("analysis", standalone_question="니트 월별 온라인 판매 추이", is_followup=False)
res = run_turn("확정한 카테고리 월별 추이 보여줘", T, LLM, graph=graph)
check("manage_context: 대화 판단 입력에 확정된 기획전 정보", "[확정된 기획전 정보]" in intent_stub.inputs[-1]
      and "카테고리: 니트" in intent_stub.inputs[-1])
check("manage_context: Agent 요청에 campaign 전달", AGENT_CALLS[-1].campaign["values"].get("category") == "니트")

# 반려 · 수정 · 취소
for answer_text, kind in (("반려", "reject"), ("카디건으로 수정해줘", "revise"), ("취소", "cancel")):
    plan("decision", updates=[StateUpdate(key="category", value="카디건")])
    run_turn("카테고리 카디건으로 바꿀래", T, LLM, graph=graph)
    res = run_turn(answer_text, T, LLM, graph=graph)
    check(f"{kind}: 확정값 유지(니트) · 대기 해제", values()["campaign"]["values"]["category"] == "니트"
          and pending_interrupt(graph, T) is None and res["turn_type"] == "approval")

# ================================================================ Agent 결과의 decisions / approval_request
AGENT_EXTRA.update(decisions={"theme": "하프집업 니트"})
plan("analysis", standalone_question="하프집업 니트 분석", is_followup=False)
run_turn("하프집업 분석해줘", T, LLM, graph=graph)
check("Agent decisions → 바로 반영", values()["campaign"]["values"].get("theme") == "하프집업 니트")
AGENT_EXTRA.clear()
AGENT_EXTRA.update(approval_request={"title": "주력 상품 확정", "updates": {"products": "Sweater, Trousers"}})
res = run_turn("주력 상품 후보 뽑아줘", T, LLM, graph=graph)
check("Agent approval_request → 분석 답변 + 승인 대기", res["turn_type"] == "analysis"
      and "[승인 요청]" in res["final_answer"] and pending_interrupt(graph, T) is not None)
run_turn("네", T, LLM, graph=graph)
check("Agent 승인 요청 승인 → products 저장 (source=general_analysis)",
      values()["campaign"]["values"].get("products") == "Sweater, Trousers"
      and values()["campaign"]["log"][-1]["source"] == "general_analysis")
AGENT_EXTRA.clear()

# ================================================================ 대화별 분리 · 자동 리셋 · 승인 끄기
check("다른 사용자 대화에는 확정값 없음", not (values("dm:U_OTHER") or {}).get("campaign"))
real_now = context._now
base = real_now()
context._now = lambda: base + timedelta(hours=settings.AUTO_RESET_HOURS + 1)
plan("chitchat", reply="안녕하세요")
res = run_turn("안녕", T, LLM, graph=graph)
context._now = real_now
check("12시간 자동 리셋: 대화는 초기화, 확정값은 유지", res["conversation_reset"]
      and values()["campaign"]["values"].get("category") == "니트" and not values().get("last_analysis"))

settings.REQUIRE_APPROVAL_FOR_DECISIONS = False
plan("decision", updates=[StateUpdate(key="schedule", value="12/5부터 4주간")])
res = run_turn("기획전은 12/5부터 4주간", T, LLM, graph=graph)
settings.REQUIRE_APPROVAL_FOR_DECISIONS = True
check("REQUIRE_APPROVAL_FOR_DECISIONS=False → 바로 저장", values()["campaign"]["values"].get("schedule") == "12/5부터 4주간"
      and pending_interrupt(graph, T) is None)

# update_conversation_state 단위
c = update_conversation_state({}, [{"key": "target", "value": "20대 여성"}, {"key": "unknown", "value": "x"}])
check("update_conversation_state: 등록 항목만 저장", c["values"] == {"target": "20대 여성"})
check("manage_workflow: 단계별 다음 Agent 규칙 자리", workflow.next_agent_for_stage({"stage": "x"}, {"a"}) is None)

# ================================================================ Slack 전달
class FakeSlack:
    def __init__(self):
        self.messages, self.files = [], []

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs.get("text"))
        return {"ok": True, "ts": "1.0"}

    def files_upload_v2(self, **kwargs):
        self.files.append(kwargs.get("filename"))
        return {"ok": True}


plan("decision", updates=[StateUpdate(key="target", value="20·30대 여성")])
res = run_turn("타깃은 20·30대 여성으로", T, LLM, graph=graph)
slack = FakeSlack()
deliver_to_slack(res, slack, "D1")
check("Slack: 승인 질문 전달 · 파일 없음", any("[승인 요청]" in str(m) for m in slack.messages) and not slack.files)
res = run_turn("승인", T, LLM, graph=graph)
slack = FakeSlack()
deliver_to_slack(res, slack, "D1")
check("Slack: 승인 결과 전달", any("저장했습니다" in str(m) for m in slack.messages))

print(f"\n==== {sum(results)}/{len(results)} 통과 ====")
sys.exit(0 if all(results) else 1)
