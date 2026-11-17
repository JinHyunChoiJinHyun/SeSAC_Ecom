"""Orchestrator · 특화 Agent 등록부 테스트 (LLM 대신 고정 응답, OpenAI 키 불필요).

가짜 특화 Agent 'demo_planning'을 등록해, 등록만으로 다음이 동작하는지 확인합니다.
- Agent가 1개면 LLM 호출 없이 바로 선택
- Orchestrator(LLM 판단)로 특화 Agent 선택 → 결과가 Slack 전달(CSV)까지 이어짐
- 후속 질문은 직전 Agent로 (KEEP_ROUTE_ON_FOLLOWUP), 끄면 다시 판단
- 등록되지 않은 이름·LLM 실패 → 기본 Agent
- 직전 분석 요약은 같은 Agent일 때만 전달
- 결과 형식 위반(final_answer 없음) → error 처리
- 등록부 검증(중복·예약 이름·기본 Agent 수)

실행: python tests/orchestrator_stub_test.py
"""

import dataclasses
import os
import sys
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
print(f"[Orchestrator 테스트] 프로젝트 경로: {PROJECT}")

import pandas as pd  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent_system.orchestrator.tools import intent as conv  # noqa: E402
from agent_system.orchestrator import settings as conversation_settings  # noqa: E402
from agent_system.orchestrator.tools.intent import IntentDecision as ConversationDecision  # noqa: E402
from agent_system.orchestrator.tools import routing as orch  # noqa: E402
from agent_system.orchestrator import registry  # noqa: E402
from agent_system.orchestrator.tools.routing import RouteDecision  # noqa: E402
from agent_system.orchestrator.registry import AgentSpec  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


# ------------------------------------------------------------------ 가짜 Agent 실행 함수
CALLS = []


def general_stub(request):
    CALLS.append(("general_analysis", request))
    return {
        "final_answer": f"[일반 분석] {request.question}",
        "dataframe": pd.DataFrame({"product_type_name": ["Dress"], "purchase_count": [10]}),
        "sql_query": "SELECT 'GENERAL_SQL'",
        "data_purpose": "일반 분석",
    }


def demo_run(request):
    CALLS.append(("demo_planning", request))
    return {
        "final_answer": f"[신제품 기획] {request.question}",
        "dataframe": pd.DataFrame({"insight": ["보온성"], "mention_ratio": [0.42]}),
        "data_purpose": "니트 신제품 기획 인사이트",
    }


def broken_run(request):
    return {"dataframe": "not a dataframe"}  # final_answer 없음


# 기본 Agent 실행 함수만 가짜로 교체 (실제 분석 그래프·LLM 미사용)
registry.AGENTS[0] = dataclasses.replace(registry.AGENTS[0], runner="__main__:general_stub")

# ------------------------------------------------------------------ 1. Agent 1개
LLM_CALLS = []


class OrchStub:
    def __init__(self, route):
        self.route = route

    def invoke(self, payload, *args, **kwargs):
        LLM_CALLS.append(payload["messages"][-1].content)
        if self.route == "RAISE":
            raise RuntimeError("LLM 오류 흉내")
        return {"structured_response": RouteDecision(route=self.route, reason="stub")}


orch.get_routing_agent = lambda cfg=None: OrchStub("general_analysis")
decision = orch.route_request("아무 질문")
check("1 Agent 1개 → LLM 호출 없이 선택", decision.route == "general_analysis" and not LLM_CALLS)

# ------------------------------------------------------------------ 등록부 검증
for label, bad in (
    ("중복 이름", AgentSpec(name="general_analysis", label="x", description="x", runner="m:f")),
    ("예약 이름", AgentSpec(name="orchestrator", label="x", description="x", runner="m:f")),
    ("기본 Agent 2개", AgentSpec(name="second_default", label="x", description="x", runner="m:f", is_default=True)),
):
    registry.AGENTS.append(bad)
    try:
        registry._validate_registry()
        ok = False
    except ValueError:
        ok = True
    registry.AGENTS.pop()
    check(f"등록부 검증: {label} 거부", ok)

# ------------------------------------------------------------------ 특화 Agent 등록
registry.AGENTS.append(AgentSpec(
    name="demo_planning", label="신제품 기획(데모)",
    description="카테고리 신제품 기획 인사이트 보고서", examples=("니트 신제품 기획 인사이트 줘",),
    runner="__main__:demo_run",
))
registry._validate_registry()

from agent_system.orchestrator.graph import build_graph as build_conversation_graph, run_turn as run_conversation_turn  # noqa: E402
from interfaces.slack.delivery import deliver_to_slack  # noqa: E402

graph = build_conversation_graph(InMemorySaver())
nodes = set(graph.get_graph().nodes)
check("그래프에 orchestrator · 각 Agent 노드 (Conversation 노드 없음)",
      {"orchestrator", "general_analysis", "demo_planning"} <= nodes and "conversation_agent" not in nodes,
      str(sorted(nodes)))
meeting = {"market_analysis", "product_analysis", "targeting", "content_generation", "validation", "reporting",
           "campaign_execution"}
check("회의 결정 전문 Agent 7개 등록(구현 전이라 꺼짐) · 그래프에는 없음",
      meeting <= {spec.name for spec in registry.get_agents(enabled_only=False)}
      and not (meeting & {spec.name for spec in registry.get_agents()}) and not (meeting & nodes))
check("Orchestrator 프롬프트에 등록 Agent 설명", "demo_planning" in orch.build_routing_prompt()
      and "신제품 기획(데모)" in conv.build_intent_prompt())


class ConvStub:
    plan = None

    def invoke(self, *args, **kwargs):
        return {"structured_response": self.plan}


conv_stub = ConvStub()
conv.get_intent_agent = lambda cfg=None: conv_stub
LLM = {"provider": "stub", "model": "stub"}
THREAD = "dm:U_ORCH"


def analysis(question, followup=False):
    conv_stub.plan = ConversationDecision(intent="analysis", standalone_question=question,
                                          is_followup=followup, reason="stub")


# ------------------------------------------------------------------ 2. LLM 판단으로 특화 Agent 선택
orch.get_routing_agent = lambda cfg=None: OrchStub("demo_planning")
analysis("니트 신제품 기획 인사이트 줘")
r2 = run_conversation_turn("니트 신제품 기획 인사이트 줘", THREAD, LLM, graph=graph)
check("2 Orchestrator → 신제품 기획 Agent", r2.get("route") == "demo_planning" and CALLS[-1][0] == "demo_planning")
check("2 결과에 route·라벨·이유", r2.get("route_label") == "신제품 기획(데모)" and r2.get("route_reason") == "stub")


class FakeSlack:
    def __init__(self):
        self.files = []

    def chat_postMessage(self, **kwargs):
        return {"ok": True, "ts": "1.0"}

    def files_upload_v2(self, **kwargs):
        self.files.append(kwargs.get("filename"))
        return {"ok": True}


slack = FakeSlack()
delivery = deliver_to_slack(result=r2, client=slack, channel_id="C1", base_dir=PROJECT)
check("2 특화 Agent 결과도 Slack 전달 + CSV", delivery["status"] == "success" and len(slack.files) == 1, str(slack.files))
saved = graph.get_state({"configurable": {"thread_id": THREAD}}).values
check("2 직전 분석에 처리 Agent 저장", saved["last_analysis"].get("route") == "demo_planning")

# ------------------------------------------------------------------ 3. 후속 질문 → 같은 Agent
LLM_CALLS.clear()
orch.get_routing_agent = lambda cfg=None: OrchStub("general_analysis")  # 호출되면 일반 분석을 고름
analysis("니트 신제품 기획 인사이트를 20대 기준으로 줘", followup=True)
r3 = run_conversation_turn("20대 기준으로", THREAD, LLM, graph=graph)
check("3 후속 질문 → 직전 Agent 유지, LLM 미호출", r3.get("route") == "demo_planning" and not LLM_CALLS)
check("3 같은 Agent → 직전 분석 요약 전달", CALLS[-1][1].last_analysis.get("route") == "demo_planning")

# ------------------------------------------------------------------ 4. KEEP_ROUTE_ON_FOLLOWUP=False
conversation_settings.KEEP_ROUTE_ON_FOLLOWUP = False
analysis("니트 신제품 기획을 남성 기준으로", followup=True)
r4 = run_conversation_turn("남성은?", THREAD, LLM, graph=graph)
check("4 KEEP_ROUTE_ON_FOLLOWUP=False → LLM이 다시 판단", r4.get("route") == "general_analysis" and LLM_CALLS)
check("4 다른 Agent로 바뀌면 직전 분석 요약을 넘기지 않음", CALLS[-1][1].last_analysis == {})
conversation_settings.KEEP_ROUTE_ON_FOLLOWUP = True

# ------------------------------------------------------------------ 5·6. 잘못된 이름 / LLM 실패 → 기본 Agent
orch.get_routing_agent = lambda cfg=None: OrchStub("없는_agent")
analysis("새 질문 A")
r5 = run_conversation_turn("새 질문 A", "dm:U_ORCH2", LLM, graph=graph)
check("5 등록되지 않은 Agent 이름 → 기본 Agent", r5.get("route") == "general_analysis", r5.get("route_reason"))
orch.get_routing_agent = lambda cfg=None: OrchStub("RAISE")
analysis("새 질문 B")
r6 = run_conversation_turn("새 질문 B", "dm:U_ORCH3", LLM, graph=graph)
check("6 Orchestrator LLM 실패 → 기본 Agent", r6.get("route") == "general_analysis", r6.get("route_reason"))

# ------------------------------------------------------------------ 7. 일상 대화는 Orchestrator를 거치지 않음
LLM_CALLS.clear()
before = len(CALLS)
conv_stub.plan = ConversationDecision(intent="chitchat", reply="안녕하세요", reason="인사")
r7 = run_conversation_turn("안녕", "dm:U_ORCH4", LLM, graph=graph)
check("7 일상 대화 → Orchestrator·Agent 미실행", r7["turn_type"] == "chitchat" and len(CALLS) == before and not LLM_CALLS)

# ------------------------------------------------------------------ 8. 결과 형식 위반
normalized = registry.normalize_agent_result("broken", broken_run(None))
check("8 final_answer 없음 → error 채움", bool(normalized.get("error")) and normalized.get("dataframe") is None)
check("8 dict가 아님 → error", bool(registry.normalize_agent_result("broken", "text").get("error")))

print(f"\n==== {sum(results)}/{len(results)} 통과 ====")
sys.exit(0 if all(results) else 1)
