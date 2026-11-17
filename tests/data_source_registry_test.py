"""데이터 출처 등록부(common/data_sources.py) 범용성 테스트.

실제 무신사·유튜브 데이터 없이, 임시 폴더에 가짜 출처 'demo'(유튜브 조회수 형태)를 만들어
등록부에 추가했을 때 코드 수정 없이 다음이 모두 따라오는지 확인합니다.

- DB 연결(ATTACH)·SQL 허용 범위·테이블 목록
- Data / Prep / Visualization / Orchestrator 대화 판단 프롬프트
- 0행 재시도용 실제 값 힌트
- 답변의 데이터 출처 표시·주의 문구, Slack 표 라벨
- 분석 그래프 → Slack 전송(PNG + CSV)까지 E2E (LLM 대신 고정 계획)
- 파일이 없는 선택 출처는 자동으로 빠지는지

실행: python tests/data_source_registry_test.py
임시 파일만 만들고 끝나면 지웁니다. 실제 DB·문서는 수정하지 않습니다.
"""

import json
import os
import shutil
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
print(f"[등록부 테스트] 프로젝트 경로: {PROJECT}")

import duckdb  # noqa: E402

import common.data_sources as registry  # noqa: E402
from common.data_sources import DataSource  # noqa: E402

# ------------------------------------------------------------------ 가짜 출처 준비
tmp = Path(tempfile.mkdtemp(prefix="cs_registry_"))
demo_db = tmp / "demo_data.duckdb"
con = duckdb.connect(str(demo_db))
con.execute("CREATE SCHEMA demo")
con.execute("""
    CREATE TABLE demo.items AS
    SELECT * FROM (VALUES
        ('v001', 'knit',   12000, TIMESTAMP '2026-11-01 09:00:00'),
        ('v002', 'knit',    8000, TIMESTAMP '2026-11-02 09:00:00'),
        ('v003', 'denim',  15000, TIMESTAMP '2026-11-01 09:00:00'),
        ('v004', 'coat',    3000, TIMESTAMP '2026-11-03 09:00:00')
    ) AS t(video_id, category, view_count, collected_at)
""")
con.close()

# 실제 문서를 복사한 임시 문서 폴더 + demo 문서
docs = tmp / "source_docs"
shutil.copytree(registry.SOURCE_DOCS_DIR, docs)
(docs / "demo").mkdir()
(docs / "demo" / "data.md").write_text(
    "[demo schema — 데모 영상 조회수]\n"
    "- demo.items: video_id, category('knit','denim','coat'), view_count, collected_at\n"
    "DEMO_DATA_DOC_MARKER\n",
    encoding="utf-8",
)
(docs / "demo" / "visualization.md").write_text("DEMO_VIZ_DOC_MARKER\n", encoding="utf-8")
registry.SOURCE_DOCS_DIR = docs

registry.DATA_SOURCES.append(DataSource(
    key="demo", label="데모", display="데모 영상 조회수 (수집일 기준)",
    capability="데모 영상 조회수 집계 (테스트용)",
    default_path=str(demo_db), path_env="CART_SIGNAL_DEMO_DB_TEST", catalog="demo_data",
    schema="demo", answer_notes=("데모 조회수는 수집 시점 값입니다.",),
    example_questions=("데모 카테고리별 조회수",),
    id_columns=("video_id",), timestamp_columns=("collected_at",), entity_columns=("video_id",),
    column_labels={"view_count": "조회수"}, column_priority={"view_count": 99},
    category_hint_columns={"demo.items": ["category"]},
))
# 파일이 없는 선택 출처 → 자동 제외되어야 함
registry.DATA_SOURCES.append(DataSource(
    key="ghost", label="유령", display="없는 출처", capability="GHOST_CAPABILITY",
    default_path=str(tmp / "missing.duckdb"), path_env="CART_SIGNAL_GHOST_DB_TEST",
    catalog="ghost_data", schema="ghost",
))
registry._validate_registry()

# 등록부를 바꾼 뒤에 나머지 모듈을 불러옵니다 (모듈 로드 시 등록부를 읽는 설정 포함)
from langchain_core.messages import HumanMessage  # noqa: E402

import agent_system.common_tools.nodes as nodes  # noqa: E402
from agent_system.common_tools.data_workflow import DataQueryPlan, build_data_agent_prompt  # noqa: E402
from agent_system.common_tools.execution_tools.data_tools import (  # noqa: E402
    build_filter_value_hint, execute_sql_to_dataframe, get_table_names, validate_select_sql,
)
from agent_system.common_tools.execution_tools.prep_tools import TIMESTAMP_COLUMN_CANDIDATES  # noqa: E402
from agent_system.common_tools.execution_tools.visualization_tools import is_semantic_identifier  # noqa: E402
from agent_system.common_tools.prep_workflow import build_prep_agent_prompt  # noqa: E402
from agent_system.common_tools.router import RouteDecision  # noqa: E402
from agent_system.common_tools.visualization_workflow import (  # noqa: E402
    VisualizationPlan, build_visualization_agent_prompt,
)
from agent_system.orchestrator.tools.intent import build_intent_prompt as build_conversation_prompt  # noqa: E402
from interfaces.slack import table_formatter as slack_table_formatter  # noqa: E402
from interfaces.slack.delivery import deliver_to_slack  # noqa: E402
from agent_system.specialized_agents.general_analysis_agent import get_general_analysis_graph  # noqa: E402
from common.db_client import get_analysis_connection  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


available = registry.source_keys()
check("선택 출처: 파일 있는 demo 포함, 없는 ghost 제외", "demo" in available and "ghost" not in available,
      str(available))

# ------------------------------------------------------------------ DB 연결·SQL
con = get_analysis_connection()
rows = con.execute("SELECT category, SUM(view_count) FROM demo.items GROUP BY 1 ORDER BY 2 DESC").fetchall()
hm_rows = con.execute("SELECT COUNT(*) FROM hm.articles").fetchone()[0]
con.close()
check("demo + hm 동시 조회 (하나의 읽기 전용 연결)", rows[0][0] == "knit" and hm_rows > 0, f"{rows} / hm.articles={hm_rows}")
check("테이블 목록에 demo.items", "demo.items" in get_table_names())
check("SQL 검증 통과", validate_select_sql("SELECT * FROM demo.items")[0])
df, error = execute_sql_to_dataframe("SELECT 1 FROM ghost.items")
check("등록 안 된(파일 없는) 출처 조회는 실패", df is None and error, str(error)[:80])
try:
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{demo_db.as_posix()}' AS d (READ_ONLY)")
    con.close()
    read_only_ok = True
except Exception:
    read_only_ok = False
check("원본 파일 읽기 전용 접근 가능", read_only_ok)

# ------------------------------------------------------------------ 프롬프트
data_prompt = build_data_agent_prompt()
check("Data 프롬프트: demo 문서·key 포함, ghost 제외",
      "DEMO_DATA_DOC_MARKER" in data_prompt and '"demo"' in data_prompt and "ghost" not in data_prompt)
check("Data 프롬프트: 기존 hm 문서 유지", "hm.transactions" in data_prompt and "Ladieswear" in data_prompt)
check("Prep 프롬프트: demo 날짜 컬럼", "collected_at" in build_prep_agent_prompt())
viz_prompt = build_visualization_agent_prompt()
check("Visualization 프롬프트: demo 문서·ID 컬럼", "DEMO_VIZ_DOC_MARKER" in viz_prompt and "video_id" in viz_prompt)
conv_prompt = build_conversation_prompt()
check("Conversation 프롬프트: demo 포함, ghost 제외",
      "데모 영상 조회수 집계" in conv_prompt and "GHOST_CAPABILITY" not in conv_prompt)
check("전처리 날짜 후보에 collected_at", "collected_at" in TIMESTAMP_COLUMN_CANDIDATES)
check("시각화 ID 판단에 video_id", is_semantic_identifier("video_id"))
check("Slack 표 라벨: view_count → 조회수", slack_table_formatter._LABELS.get("view_count") == "조회수")
hint = build_filter_value_hint("SELECT * FROM demo.items WHERE category = 'sweater'")
check("0행 재시도 힌트에 demo 실제 값", "demo.items.category" in hint and "'knit'" in hint, hint[:120])

# ------------------------------------------------------------------ E2E (분석 그래프 → Slack)
class FakeAgent:
    def __init__(self, plan):
        self.plan = plan

    def invoke(self, *args, **kwargs):
        return {"structured_response": self.plan}


def fake_route(user_query, completed_steps, has_dataframe, has_processed_dataframe,
               has_visualization_result, model_config=None):
    if not has_dataframe:
        return RouteDecision(next_agent="data_agent", reason="stub")
    if "visualization" not in completed_steps:
        return RouteDecision(next_agent="visualization_agent", reason="stub")
    return RouteDecision(next_agent="final_response", reason="stub")


nodes.route_request = fake_route
nodes.get_data_agent = lambda cfg=None: FakeAgent(DataQueryPlan(
    sql_query="SELECT category, SUM(view_count) AS view_count FROM demo.items GROUP BY 1 ORDER BY 2 DESC",
    purpose="데모 카테고리별 조회수", data_sources=["demo", "없는출처"]))
nodes.get_visualization_agent = lambda cfg=None: FakeAgent(VisualizationPlan(
    chart_type="bar", x_column="category", y_column="view_count", title="데모 카테고리별 조회수", reason="비교"))

result = get_general_analysis_graph().invoke(
    {"messages": [HumanMessage(content="데모 카테고리별 조회수 그래프")], "completed_steps": [],
     "llm_config": {"provider": "stub", "model": "stub"}},
    config={"recursion_limit": 30},
)
answer = result.get("final_answer", "")
check("E2E: 오류 없이 그래프 생성", not result.get("error") and result.get("visualization_path"), str(result.get("error")))
check("E2E: data_sources는 등록된 key만", result.get("data_sources") == ["demo"], str(result.get("data_sources")))
check("E2E: 답변에 출처 표시·주의 문구", "데모 영상 조회수 (수집일 기준)" in answer and "수집 시점 값" in answer)


class FakeSlack:
    def __init__(self):
        self.files, self.blocks = [], []

    def chat_postMessage(self, **kwargs):
        if kwargs.get("blocks"):
            self.blocks.append(json.dumps(kwargs["blocks"], ensure_ascii=False))
        return {"ok": True, "ts": "1.0"}

    def files_upload_v2(self, **kwargs):
        self.files.append(kwargs.get("filename"))
        return {"ok": True}


slack = FakeSlack()
delivery = deliver_to_slack(result=result, client=slack, channel_id="C1", base_dir=PROJECT)
check("E2E: Slack 전송 PNG + CSV", delivery["status"] == "success" and len(slack.files) == 2, str(slack.files))

# ------------------------------------------------------------------ 수집 중 잠금
# 다른 프로세스가 demo 파일을 쓰기로 열고 있는 상황(크롤러 적재 중)을 흉내냅니다.
import subprocess  # noqa: E402

writer = subprocess.Popen(
    [sys.executable, "-c",
     "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); "
     "print('locked', flush=True); time.sleep(60)", str(demo_db)],
    stdout=subprocess.PIPE, text=True,
)
try:
    check("잠금 프로세스 시작", writer.stdout.readline().strip() == "locked")

    from common.db_client import open_duckdb_connection  # noqa: E402
    con, skipped = open_duckdb_connection()
    hm_count = con.execute("SELECT COUNT(*) FROM hm.articles").fetchone()[0]
    con.close()
    check("demo 잠김 → demo만 건너뛰고 H&M 조회는 정상",
          hm_count > 0 and any(item["key"] == "demo" and item["reason"] == "locked_or_error" for item in skipped),
          str([(item["key"], item["reason"]) for item in skipped]))

    df, error = execute_sql_to_dataframe("SELECT COUNT(*) FROM demo.items")
    check("잠긴 출처 SQL → 잠시 후 다시 시도 안내", df is None and "잠시 후 다시" in str(error), str(error))

    df, error = execute_sql_to_dataframe("SELECT COUNT(*) AS n FROM hm.customers")
    check("같은 시점 H&M SQL은 정상", df is not None and int(df.iloc[0, 0]) > 0, str(error))

    locked_result = get_general_analysis_graph().invoke(
        {"messages": [HumanMessage(content="데모 카테고리별 조회수 그래프")], "completed_steps": [],
         "llm_config": {"provider": "stub", "model": "stub"}},
        config={"recursion_limit": 30},
    )
    check("그래프: 재시도 없이 안내 문구로 종료",
          str(locked_result.get("final_answer", "")).startswith("데이터 출처를 일시적으로 사용할 수 없습니다")
          and not locked_result.get("data_retry_count"),
          str(locked_result.get("final_answer", ""))[:80])

    import slack_bot  # noqa: E402
    passed_through = slack_bot.run_slack_question("데모 그래프", lambda q: locked_result)
    check("Slack: 실패 처리 대신 안내 문구 전달", slack_bot._is_user_facing_error(passed_through))
finally:
    writer.kill()
    writer.wait()

con = get_analysis_connection()
rows_after = con.execute("SELECT COUNT(*) FROM demo.items").fetchone()[0]
con.close()
check("잠금 해제 후 demo 다시 조회", rows_after == 4)

# ------------------------------------------------------------------ 정리
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n==== {sum(results)}/{len(results)} 통과 ====")
sys.exit(0 if all(results) else 1)
