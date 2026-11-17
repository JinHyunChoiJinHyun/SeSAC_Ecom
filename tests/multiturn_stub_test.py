"""멀티턴 대화 E2E 테스트 (LLM 대신 고정 응답 stub 사용, OpenAI 키 불필요).

확인 항목
1. 일상 대화(chitchat)는 데이터 조회 없이 바로 답한다
2. 분석 결과 요약이 기억에 남아 다음 턴 Orchestrator 대화 판단 입력에 들어간다
3. 후속 분석 요청에는 직전 SQL이 Data Agent 입력으로 전달된다
4. 봇을 재시작(그래프 재생성)해도 SQLite 기억으로 대화가 이어진다
5. 다른 대화(thread)에는 기억이 섞이지 않는다
6. chitchat 결과도 Delivery(Slack 전송)를 통과하고 CSV는 붙지 않는다

실행: python tests/multiturn_stub_test.py   (또는 WORKS 바로 아래에서 python multiturn_stub_test.py)
기억 파일은 임시 폴더에 만들고 끝나면 지웁니다. 실제 runtime/ 기억에는 쓰지 않습니다.
"""

import os
import sqlite3
import sys
import tempfile
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
print(f"[멀티턴 테스트] 프로젝트 경로: {PROJECT}")

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

import agent_system.common_tools.nodes as nodes  # noqa: E402
from agent_system.common_tools.data_workflow import DataQueryPlan  # noqa: E402
from agent_system.common_tools.router import RouteDecision, detect_requested_agents  # noqa: E402
from agent_system.common_tools.visualization_workflow import VisualizationPlan  # noqa: E402
from agent_system.orchestrator.tools import intent as conv  # noqa: E402
from agent_system.orchestrator.tools.intent import IntentDecision as ConversationDecision  # noqa: E402
from agent_system.orchestrator.tools import context as conversation_graph_module  # noqa: E402
from agent_system.orchestrator import settings as conversation_settings  # noqa: E402
from agent_system.orchestrator.graph import build_graph as build_conversation_graph, run_turn as run_conversation_turn  # noqa: E402
from interfaces.slack.delivery import deliver_to_slack  # noqa: E402


class RecordingAgent:
    """호출 입력을 기록하고 정해 둔 계획을 반환하는 stub Agent."""

    def __init__(self):
        self.plan = None
        self.inputs = []

    def invoke(self, payload=None, *args, **kwargs):
        self.inputs.append(payload["messages"][-1].content)
        return {"structured_response": self.plan}


conversation_stub = RecordingAgent()
data_stub = RecordingAgent()
viz_stub = RecordingAgent()

conv.get_intent_agent = lambda cfg=None: conversation_stub
nodes.get_data_agent = lambda cfg=None: data_stub
nodes.get_visualization_agent = lambda cfg=None: viz_stub


def fake_route(user_query, completed_steps, has_dataframe, has_processed_dataframe,
               has_visualization_result, model_config=None):
    requested = detect_requested_agents(user_query)
    if not has_dataframe:
        return RouteDecision(next_agent="data_agent", reason="stub")
    if requested["visualization_agent"] and "visualization" not in completed_steps:
        return RouteDecision(next_agent="visualization_agent", reason="stub")
    return RouteDecision(next_agent="final_response", reason="stub")


nodes.route_request = fake_route


class FakeSlack:
    def __init__(self):
        self.messages, self.files = [], []

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs.get("text"))
        return {"ok": True, "ts": "1.0"}

    def files_upload_v2(self, **kwargs):
        self.files.append(kwargs.get("filename"))
        return {"ok": True}


LLM = {"provider": "stub", "model": "stub"}
WOMEN_SQL = """SELECT a.product_type_name, COUNT(*) AS purchase_count,
       COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS purchase_share
FROM hm.transactions t JOIN hm.articles a ON t.article_id = a.article_id
WHERE a.index_group_name = 'Ladieswear'
GROUP BY a.product_type_name ORDER BY purchase_count DESC LIMIT 10"""
MEN_SQL = WOMEN_SQL.replace("'Ladieswear'", "'Menswear'")

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


tmp_dir = Path(tempfile.mkdtemp(prefix="cs_memory_"))
db_path = tmp_dir / "memory.sqlite"


def new_graph():
    """봇 실행 1회에 해당하는 그래프 (같은 SQLite 파일 사용)."""
    connection = sqlite3.connect(str(db_path), check_same_thread=False)
    return build_conversation_graph(SqliteSaver(connection))


THREAD = "dm:U_TEST"
graph = new_graph()

# ---------------------------------------------------------- 1. 인사
conversation_stub.plan = ConversationDecision(
    intent="chitchat", reply="안녕하세요. H&M·Amazon 데이터 조회와 시각화를 도와드립니다.", reason="인사")
r1 = run_conversation_turn("안녕", THREAD, LLM, graph=graph)
check("1 인사 → chitchat", r1["turn_type"] == "chitchat" and "안녕하세요" in r1["final_answer"])
check("1 데이터 조회 안 함", len(data_stub.inputs) == 0)
slack = FakeSlack()
delivery = deliver_to_slack(result=r1, client=slack, channel_id="C1", base_dir=PROJECT)
check("1 chitchat Slack 전송 + 첨부 없음", delivery["status"] == "success" and not slack.files,
      f"status={delivery['status']} files={slack.files}")

# ---------------------------------------------------------- 2. 첫 분석
conversation_stub.plan = ConversationDecision(
    intent="analysis",
    standalone_question="H&M 여성 의류 상품 유형별 구매 건수 상위 10개와 구매 비중을 알려줘",
    is_followup=False, reason="새 분석")
data_stub.plan = DataQueryPlan(sql_query=WOMEN_SQL, purpose="H&M 여성 상품 유형별 구매 Top 10과 비중",
                               data_sources=["hm"])
r2 = run_conversation_turn("여성 의류 상품 유형별 구매 top10이랑 비중 알려줘", THREAD, LLM, graph=graph)
check("2 분석 실행", r2["turn_type"] == "analysis" and len(r2["dataframe"]) == 10,
      f"rows={len(r2['dataframe'])}")
check("2 첫 분석에는 직전 SQL 없음", "[직전 분석 SQL" not in data_stub.inputs[-1])
top1 = str(r2["dataframe"].iloc[0]["product_type_name"])

# ---------------------------------------------------------- 3. 결과에 대한 질문
conversation_stub.plan = ConversationDecision(intent="chitchat", reply=f"1위는 {top1}입니다.", reason="직전 결과")
r3 = run_conversation_turn("1위가 뭐였지?", THREAD, LLM, graph=graph)
conv_input = conversation_stub.inputs[-1]
check("3 이전 대화가 입력에 포함", "여성 의류 상품 유형별 구매 top10" in conv_input)
check("3 직전 분석 SQL·미리보기가 입력에 포함", "Ladieswear" in conv_input and top1 in conv_input)
check("3 chitchat 답변", r3["turn_type"] == "chitchat")

# ---------------------------------------------------------- 4. 후속 분석 (조건 변경 + 그래프)
conversation_stub.plan = ConversationDecision(
    intent="analysis",
    standalone_question="H&M 남성 의류 상품 유형별 구매 건수 상위 10개와 구매 비중을 막대그래프로 보여줘",
    is_followup=True, reason="성별만 변경")
data_stub.plan = DataQueryPlan(sql_query=MEN_SQL, purpose="H&M 남성 상품 유형별 구매 Top 10과 비중",
                               data_sources=["hm"])
viz_stub.plan = VisualizationPlan(chart_type="bar", x_column="product_type_name", y_column="purchase_count",
                                  title="H&M 남성 상품 유형별 구매 Top 10", reason="Top-N")
r4 = run_conversation_turn("남성은? 그래프로", THREAD, LLM, graph=graph)
check("4 후속 요청에 직전 SQL 전달", "[직전 분석 SQL" in data_stub.inputs[-1] and "Ladieswear" in data_stub.inputs[-1])
check("4 남성 결과 + 그래프", r4.get("visualization_path") and "visualization" in r4.get("completed_steps", []),
      str(r4.get("visualization_path")))
slack = FakeSlack()
delivery = deliver_to_slack(result=r4, client=slack, channel_id="C1", base_dir=PROJECT)
check("4 Slack에 PNG + CSV", len(slack.files) == 2, str(slack.files))
check("4 다시 쓴 질문을 답변 맨 위에 표시 (SHOW_REWRITTEN_QUESTION=True)",
      r4["final_answer"].startswith('이전 대화를 반영해 "H&M 남성 의류'), r4["final_answer"][:70])


# ---------------------------------------------------------- 5. 재시작 후 이어짐
graph = new_graph()
conversation_stub.plan = ConversationDecision(intent="chitchat", reply="여성과 남성 상품 유형 Top 10을 보셨습니다.",
                                              reason="기록")
run_conversation_turn("지금까지 뭐 봤지?", THREAD, LLM, graph=graph)
conv_input = conversation_stub.inputs[-1]
check("5 재시작 후에도 이전 대화 유지", "안녕" in conv_input and "남성은? 그래프로" in conv_input)
check("5 직전 분석 = 남성", "Menswear" in conv_input)
check("5 기억에는 '다시 쓴 질문' 표시 문구를 저장하지 않음", "이전 대화를 반영해" not in conv_input)

# ---------------------------------------------------------- 6. 다른 대화와 분리
run_conversation_turn("안녕", "ch:C_OTHER:123.456", LLM, graph=graph)
conv_input = conversation_stub.inputs[-1]
check("6 다른 thread는 기억 분리", "(이전 대화 없음)" in conv_input and "(직전 분석 없음)" in conv_input)

# ---------------------------------------------------------- 7. 대화 판단 실패 시 원문으로 분석
def broken_agent(cfg=None):
    raise RuntimeError("LLM 오류 흉내")


conv.get_intent_agent = broken_agent
data_stub.plan = DataQueryPlan(sql_query=WOMEN_SQL, purpose="fallback", data_sources=["hm"])
r7 = run_conversation_turn("여성 상품 유형 Top 10", "dm:U_FALLBACK", LLM, graph=graph)
check("7 대화 판단 실패 → 원문으로 분석 진행", r7["turn_type"] == "analysis" and len(r7["dataframe"]) == 10)

# ---------------------------------------------------------- 8. 다시 쓴 질문 표시 끄기
conv.get_intent_agent = lambda cfg=None: conversation_stub
conversation_settings.SHOW_REWRITTEN_QUESTION = False
conversation_stub.plan = ConversationDecision(
    intent="analysis", standalone_question="H&M 여성 의류 상품 유형별 구매 건수 상위 10개와 구매 비중을 알려줘",
    is_followup=True, reason="다시 여성")
data_stub.plan = DataQueryPlan(sql_query=WOMEN_SQL, purpose="여성 다시", data_sources=["hm"])
r8 = run_conversation_turn("다시 여성으로", "dm:U_SHOW", LLM, graph=graph)
check("8 SHOW_REWRITTEN_QUESTION=False → 표시 안 함", not r8["final_answer"].startswith("이전 대화를 반영해"))
conversation_settings.SHOW_REWRITTEN_QUESTION = True

# ---------------------------------------------------------- 9. 시간 기준 자동 리셋
from datetime import timedelta  # noqa: E402

real_now = conversation_graph_module._now
RESET_THREAD = "dm:U_RESET"
conversation_stub.plan = ConversationDecision(intent="chitchat", reply="네", reason="잡담")


def at(hours):
    """기준 시각에서 hours 시간 뒤로 시계를 맞춥니다."""
    base = real_now()
    conversation_graph_module._now = lambda: base + timedelta(hours=hours)


at(0)
run_conversation_turn("RESET_TEST_첫 메시지", RESET_THREAD, LLM, graph=graph)
at(11)
r9a = run_conversation_turn("11시간 뒤", RESET_THREAD, LLM, graph=graph)
check("9 11시간 뒤 → 이어짐", "RESET_TEST_첫 메시지" in conversation_stub.inputs[-1] and not r9a["conversation_reset"])
at(11 + 12)
r9b = run_conversation_turn("12시간 넘게 지남", RESET_THREAD, LLM, graph=graph)
check("9 마지막 메시지 후 12시간 → 새 대화",
      r9b["conversation_reset"] and "(이전 대화 없음)" in conversation_stub.inputs[-1]
      and "(직전 분석 없음)" in conversation_stub.inputs[-1])
stored = graph.get_state({"configurable": {"thread_id": RESET_THREAD}}).values["messages"]
check("9 리셋 후 기억에는 이번 턴만 남음", [m.content for m in stored] == ["12시간 넘게 지남", "네"],
      str([m.content for m in stored]))

conversation_settings.AUTO_RESET_ENABLED = False
at(11 + 12 + 30)
r9c = run_conversation_turn("자동 리셋 끔", RESET_THREAD, LLM, graph=graph)
check("9 AUTO_RESET_ENABLED=False → 30시간 지나도 이어짐",
      not r9c["conversation_reset"] and "12시간 넘게 지남" in conversation_stub.inputs[-1])
conversation_settings.AUTO_RESET_ENABLED = True
conversation_graph_module._now = real_now

# ---------------------------------------------------------- 10. 이전 저장본 정리 (PRUNE_OLD_CHECKPOINTS)
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402


def history_count(target_graph, thread):
    return len(list(target_graph.get_state_history({"configurable": {"thread_id": thread}})))


def snapshots_with(target_graph, thread, text):
    return sum(
        text in [m.content for m in snap.values.get("messages", [])]
        for snap in target_graph.get_state_history({"configurable": {"thread_id": thread}})
    )


conversation_stub.plan = ConversationDecision(intent="chitchat", reply="네", reason="잡담")
check("10 기본값 PRUNE_OLD_CHECKPOINTS=False", conversation_settings.PRUNE_OLD_CHECKPOINTS is False)
check("10 False → 이전 저장본 유지", history_count(graph, THREAD) > 1, f"{history_count(graph, THREAD)}개")

conversation_settings.PRUNE_OLD_CHECKPOINTS = True
for label, target in (("SQLite", graph), ("메모리", build_conversation_graph(InMemorySaver()))):
    thread = f"dm:U_PRUNE_{label}"
    other_before = history_count(target, THREAD)
    for text in ("PRUNE_OLD_1", "PRUNE_OLD_2", "PRUNE_OLD_3"):
        run_conversation_turn(text, thread, LLM, graph=target)
    current = [m.content for m in target.get_state({"configurable": {"thread_id": thread}}).values["messages"]]
    check(f"10 True({label}) → 최신 저장본 1개만", history_count(target, thread) == 1, f"{history_count(target, thread)}개")
    check(f"10 True({label}) → 현재 대화 내용은 유지", current[0] == "PRUNE_OLD_1" and len(current) == 6, str(current))
    check(f"10 True({label}) → 다른 대화는 건드리지 않음", history_count(target, THREAD) == other_before)

    # 자동 리셋 + 정리 → 리셋 전 메시지가 어떤 저장본에도 남지 않음
    base = real_now()
    conversation_graph_module._now = lambda base=base: base + timedelta(hours=13)
    run_conversation_turn("PRUNE_AFTER_RESET", thread, LLM, graph=target)
    conversation_graph_module._now = real_now
    check(f"10 True({label}) + 리셋 → 리셋 전 메시지가 저장본에서 사라짐",
          snapshots_with(target, thread, "PRUNE_OLD_1") == 0
          and [m.content for m in target.get_state({"configurable": {"thread_id": thread}}).values["messages"]]
          == ["PRUNE_AFTER_RESET", "네"])
conversation_settings.PRUNE_OLD_CHECKPOINTS = False

# ---------------------------------------------------------- 11. 직전 그래프 형태 유지 (KEEP_PREVIOUS_CHART_TYPE)
from agent_system.orchestrator.tools.context import keep_previous_chart_type  # noqa: E402

bar = {"chart_type": "bar"}
check("11 그래프 없는 후속 → 막대그래프 추가",
      keep_previous_chart_type("남성 Top 5", "Top 5로 줄여줘", bar, True).endswith("(직전과 같이 막대그래프로 보여줘)"))
check("11 이미 그래프 요청 있음 → 그대로",
      keep_previous_chart_type("남성 Top 5 선그래프로", "선그래프로", bar, True) == "남성 Top 5 선그래프로")
check("11 '표로' 요청 → 그대로", keep_previous_chart_type("남성 Top 5", "표로 보여줘", bar, True) == "남성 Top 5")
check("11 '대표로'는 표 요청 아님",
      "막대그래프" in keep_previous_chart_type("대표 상품", "대표로 많이 팔린 건?", bar, True))
check("11 후속 아님 / 직전 그래프 없음 → 그대로",
      keep_previous_chart_type("새 질문", "새 질문", bar, False) == "새 질문"
      and keep_previous_chart_type("남성 Top 5", "Top 5로", {}, True) == "남성 Top 5")
conversation_settings.KEEP_PREVIOUS_CHART_TYPE = False
check("11 KEEP_PREVIOUS_CHART_TYPE=False → 그대로",
      keep_previous_chart_type("남성 Top 5", "Top 5로 줄여줘", bar, True) == "남성 Top 5")
conversation_settings.KEEP_PREVIOUS_CHART_TYPE = True

# E2E: 직전 막대그래프 → "Top 5로" 후속 (LLM이 그래프를 빠뜨린 경우) → 그래프 다시 생성
conv.get_intent_agent = lambda cfg=None: conversation_stub
conversation_stub.plan = ConversationDecision(
    intent="analysis", standalone_question="H&M 남성 의류 상품 유형별 구매 건수 상위 5개와 구매 비중을 보여줘",
    is_followup=True, reason="Top 5")
data_stub.plan = DataQueryPlan(sql_query=MEN_SQL.replace("LIMIT 10", "LIMIT 5"), purpose="남성 Top 5",
                               data_sources=["hm"])
r11 = run_conversation_turn("Top 5로 줄여줘", THREAD, LLM, graph=graph)
check("11 E2E: 그래프 다시 생성", bool(r11.get("visualization_path")) and len(r11["dataframe"]) == 5,
      r11.get("standalone_question", "")[-30:])

# ---------------------------------------------------------- 12. 채널 대화 ID (CHANNEL_MEMORY_SCOPE / CHANNEL_PER_USER)
from agent_system.orchestrator.tools.memory import build_slack_thread_id  # noqa: E402

# 같은 채널, 사용자 A가 새 메시지로 두 번 멘션 (서로 다른 스레드) / 사용자 B
event_a1 = {"channel_type": "channel", "channel": "C1", "ts": "100.0", "user": "UA"}
event_a2 = {"channel_type": "channel", "channel": "C1", "ts": "300.0", "user": "UA"}
event_a_in_thread = {**event_a1, "ts": "150.0", "thread_ts": "100.0"}
event_b = {"channel_type": "channel", "channel": "C1", "ts": "200.0", "thread_ts": "100.0", "user": "UB"}
dm_a = {"channel_type": "im", "channel": "D1", "ts": "1.0", "user": "UA"}

check("12 기본값 CHANNEL_MEMORY_SCOPE='channel', CHANNEL_PER_USER=True",
      conversation_settings.CHANNEL_MEMORY_SCOPE == "channel" and conversation_settings.CHANNEL_PER_USER is True)
check("12 channel: 같은 사용자의 새 메시지 멘션 → 같은 대화",
      build_slack_thread_id(event_a1) == build_slack_thread_id(event_a2) == "ch:C1:UA")
check("12 channel: 다른 사용자 → 다른 대화", build_slack_thread_id(event_b) == "ch:C1:UB")
check("12 DM은 사용자별 그대로", build_slack_thread_id(dm_a) == "dm:UA")

conv.get_intent_agent = lambda cfg=None: conversation_stub
conversation_stub.plan = ConversationDecision(intent="chitchat", reply="네", reason="잡담")
run_conversation_turn("USER_A_첫_멘션", build_slack_thread_id(event_a1), LLM, graph=graph)
run_conversation_turn("USER_B_멘션", build_slack_thread_id(event_b), LLM, graph=graph)
check("12 같은 채널에서 B는 A의 대화를 모름",
      "USER_A_첫_멘션" not in conversation_stub.inputs[-1] and "(이전 대화 없음)" in conversation_stub.inputs[-1])
run_conversation_turn("USER_A_새_메시지_멘션", build_slack_thread_id(event_a2), LLM, graph=graph)
check("12 A가 새 메시지로 멘션해도 이전 대화를 이어받음 (Slack 로그 재현)",
      "USER_A_첫_멘션" in conversation_stub.inputs[-1] and "USER_B_멘션" not in conversation_stub.inputs[-1])

conversation_settings.CHANNEL_MEMORY_SCOPE = "thread"
check("12 thread: 새 메시지 멘션 → 다른 대화",
      build_slack_thread_id(event_a1) == "ch:C1:100.0:UA" and build_slack_thread_id(event_a2) == "ch:C1:300.0:UA")
check("12 thread: 스레드 안 답글 → 스레드 대화", build_slack_thread_id(event_a_in_thread) == "ch:C1:100.0:UA")
conversation_settings.CHANNEL_PER_USER = False
check("12 thread + CHANNEL_PER_USER=False → 스레드 공유",
      build_slack_thread_id(event_a_in_thread) == build_slack_thread_id(event_b) == "ch:C1:100.0")
conversation_settings.CHANNEL_MEMORY_SCOPE = "channel"
check("12 channel + CHANNEL_PER_USER=False → 채널 공유",
      build_slack_thread_id(event_a1) == build_slack_thread_id(event_b) == "ch:C1")
conversation_settings.CHANNEL_PER_USER = True

# ---------------------------------------------------------- 13. Top-N 결과에 표본 추출 금지 (순위 순서 보존)
data_stub.plan = DataQueryPlan(sql_query=WOMEN_SQL, purpose="Top 10 그래프", data_sources=["hm"],
                               result_type="visual_sample", is_sampled=True, sample_size=10)
command = nodes.data_node({"question": "여성 상품 유형별 Top 10 그래프로", "llm_config": LLM})
check("13 LLM이 visual_sample을 골라도 LIMIT 10 결과는 full_result",
      command.update.get("data_result_type") == "full_result"
      and "USING SAMPLE" not in command.update.get("sql_query", "").upper(),
      command.update.get("data_result_type"))

# ---------------------------------------------------------- 결과
saved = graph.get_state({"configurable": {"thread_id": THREAD}}).values
print(f"\n[기억 저장 확인] {THREAD}: 메시지 {len(saved.get('messages', []))}개, "
      f"직전 분석 = {saved.get('last_analysis', {}).get('purpose')}")
check("기억에 DataFrame을 저장하지 않음", "dataframe" not in saved)

passed = sum(ok for _, ok in results)
print(f"\n==== {passed}/{len(results)} 통과 ====")
try:
    for path in tmp_dir.iterdir():
        path.unlink()
    tmp_dir.rmdir()
except OSError:
    pass
sys.exit(0 if passed == len(results) else 1)
