"""리팩터링 코드 E2E 테스트 (LLM 대신 고정 계획을 넣는 stub 사용).

실제 DB(hm_data.duckdb)와 Amazon 테스트 DB로
Router → Data → SQL Executor → Prep/Visualization → Final Response → Delivery(CSV/PNG)
경로가 끝까지 동작하는지 확인합니다. OpenAI 키를 쓰지 않습니다.
"""
import os, sys, json
from pathlib import Path
def _find_project_root() -> Path:
    """프로젝트 루트(agent_system 폴더가 있는 곳)를 찾습니다.

    - 인자를 주면 그 경로 사용: python tests/e2e_stub_test.py /path/to/WORKS
    - 인자가 없으면 스크립트 위치 → 상위 폴더 → 현재 폴더 순으로 찾습니다.
    """
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents, Path.cwd()]:
        if (candidate / "agent_system").is_dir():
            return candidate
    sys.exit("agent_system 폴더를 찾지 못했습니다. 프로젝트 경로를 인자로 주세요: python e2e_stub_test.py <WORKS 경로>")

PROJECT = _find_project_root()
print(f"[E2E] 프로젝트 경로: {PROJECT}")
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

from langchain_core.messages import HumanMessage
import agent_system.common_tools.nodes as nodes
from agent_system.common_tools.router import RouteDecision, detect_requested_agents
from agent_system.common_tools.data_workflow import DataQueryPlan
from agent_system.common_tools.prep_workflow import PrepPlan
from agent_system.common_tools.visualization_workflow import VisualizationPlan
from agent_system.specialized_agents.general_analysis_agent import get_general_analysis_graph

class FakeAgent:
    """plan이 list면 호출할 때마다 다음 계획을 반환합니다(재시도 테스트용)."""
    def __init__(self, plan):
        self.plans = list(plan) if isinstance(plan, list) else [plan]
        self.inputs = []
    def invoke(self, payload=None, *a, **k):
        try: self.inputs.append(payload["messages"][-1].content)
        except Exception: pass
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        return {"structured_response": plan}

CASES = []
def case(name, question, data_plan=None, prep_plan=None, viz_plan=None):
    CASES.append(dict(name=name, q=question, data=data_plan, prep=prep_plan, viz=viz_plan))

# Router stub: 실제 route_request의 규칙 보정을 재현하기 위해 LLM 판단만 흉내냅니다.
def fake_route(user_query, completed_steps, has_dataframe, has_processed_dataframe, has_visualization_result, model_config=None):
    from agent_system.common_tools.router import is_prediction_only_request, UNSUPPORTED_PREDICTION_REASON
    req = detect_requested_agents(user_query)
    if not completed_steps and not req["visualization_agent"] and not req["prep_agent"] and is_prediction_only_request(user_query):
        return RouteDecision(next_agent="final_response", reason=UNSUPPORTED_PREDICTION_REASON)
    if not has_dataframe:
        return RouteDecision(next_agent="data_agent", reason="stub: data 먼저")
    if req["prep_agent"] and "prep" not in completed_steps:
        return RouteDecision(next_agent="prep_agent", reason="stub")
    if req["visualization_agent"] and "visualization" not in completed_steps:
        return RouteDecision(next_agent="visualization_agent", reason="stub")
    return RouteDecision(next_agent="final_response", reason="stub: 완료")
nodes.route_request = fake_route

case("1_hm_bar_top10", "상품 유형별 온라인 판매량 Top 10을 막대그래프로 보여줘",
     DataQueryPlan(sql_query="""SELECT a.product_type_name, COUNT(*) AS purchase_count
FROM hm.transactions t JOIN hm.articles a ON t.article_id = a.article_id
WHERE t.sales_channel_id = 2 GROUP BY a.product_type_name ORDER BY purchase_count DESC LIMIT 10""",
       purpose="상품 유형별 온라인 판매량 Top 10", data_sources=["hm"]),
     viz_plan=VisualizationPlan(chart_type="bar", x_column="product_type_name", y_column="purchase_count", title="상품 유형별 온라인 판매량 Top 10", reason="Top-N"))
case("2_hm_line_monthly", "월별 채널별 판매 추이를 선그래프로 보여줘",
     DataQueryPlan(sql_query="""SELECT date_trunc('month', t_dat) AS month, COUNT(*) AS purchase_count
FROM hm.transactions GROUP BY 1 ORDER BY 1""", purpose="월별 판매 추이", data_sources=["hm"]),
     viz_plan=VisualizationPlan(chart_type="line", x_column="month", y_column="purchase_count", title="월별 구매 건수 추이", reason="추이"))
case("3_hm_corr_heatmap", "고객별 구매 횟수, 평균 가격지수, 온라인 비중, 나이의 상관관계를 보여줘",
     DataQueryPlan(sql_query="""WITH m AS (SELECT customer_id, COUNT(*) AS purchase_count, AVG(price)/0.0339*100 AS avg_price_index,
 AVG((sales_channel_id=2)::INT) AS online_ratio FROM hm.transactions GROUP BY customer_id)
SELECT m.customer_id, m.purchase_count, m.avg_price_index, m.online_ratio, c.age
FROM m JOIN hm.customers c ON m.customer_id = c.customer_id WHERE c.age IS NOT NULL""",
       purpose="고객 지표 상관관계", data_sources=["hm"], result_type="correlation_matrix",
       feature_columns=["purchase_count", "avg_price_index", "online_ratio", "age", "customer_id"]),
     viz_plan=VisualizationPlan(chart_type="heatmap", heatmap_columns=["purchase_count","avg_price_index","online_ratio","age"], title="고객 지표 상관관계", reason="3개 이상"))
case("4_hm_scatter_sample", "고객별 구매 횟수와 평균 가격지수의 관계를 보여줘",
     DataQueryPlan(sql_query="""SELECT customer_id, COUNT(*) AS purchase_count, AVG(price)/0.0339*100 AS avg_price_index
FROM hm.transactions GROUP BY customer_id""", purpose="고객 지표 관계", data_sources=["hm"],
       feature_columns=["purchase_count", "avg_price_index"]),
     viz_plan=VisualizationPlan(chart_type="scatter", x_column="purchase_count", y_column="avg_price_index", title="구매 횟수와 평균 가격지수", reason="2개"))
case("5_hm_prep_missing", "니트 구매 기록 100건을 가져와서 결측치와 중복을 확인하고 날짜 변환해줘",
     DataQueryPlan(sql_query="""SELECT t.t_dat, t.customer_id, t.article_id, t.price, c.age, c.FN
FROM hm.transactions t JOIN hm.articles a ON t.article_id=a.article_id JOIN hm.customers c ON t.customer_id=c.customer_id
WHERE a.garment_group_name='Knitwear' LIMIT 100""", purpose="니트 구매 기록 샘플", data_sources=["hm"]),
     prep_plan=PrepPlan(actions=["check_missing_values","check_duplicates","convert_timestamp","sort_by_timestamp","create_repeat_count_feature"], reason="요청"))
case("6_amazon_rating_bar", "Amazon 여성 스웨터 리뷰 별점 분포를 그래프로 보여줘",
     DataQueryPlan(sql_query="""SELECT r.rating, COUNT(*) AS review_count FROM amazon.review r
JOIN amazon.meta m ON r.parent_asin = m.parent_asin
WHERE list_contains(m.categories, 'Women') AND list_has_any(m.categories, ['Sweaters','Cardigans'])
GROUP BY r.rating ORDER BY r.rating""", purpose="Amazon 여성 스웨터 별점 분포", data_sources=["amazon"]),
     viz_plan=VisualizationPlan(chart_type="bar", x_column="rating", y_column="review_count", title="Amazon 여성 스웨터 별점 분포", reason="범주 비교"))
case("7_hm_simple_query", "구매 횟수가 가장 많은 고객 5명 알려줘",
     DataQueryPlan(sql_query="""SELECT customer_id, COUNT(*) AS purchase_count FROM hm.transactions GROUP BY customer_id ORDER BY purchase_count DESC LIMIT 5""",
       purpose="구매 횟수 상위 고객 5명", data_sources=["hm"]))
case("8_prediction_unsupported", "다음 주 고객별 구매확률을 예측해줘")
case("9_blocked_sql", "아무거나",
     DataQueryPlan(sql_query="SELECT * FROM read_parquet('/etc/passwd')", purpose="악성", data_sources=["hm"]))

case("10_zero_rows_retry", "H&M 여성 의류에서 상품 유형별 구매 건수 상위 10개와 구매 비중을 알려줘",
     [DataQueryPlan(sql_query="""SELECT a.product_type_name, COUNT(*) AS purchase_count
FROM hm.transactions t JOIN hm.articles a ON t.article_id = a.article_id
WHERE a.product_group_name = 'Ladieswear' GROUP BY a.product_type_name ORDER BY purchase_count DESC LIMIT 10""",
       purpose="잘못된 필터(0행 유도)", data_sources=["hm"]),
      DataQueryPlan(sql_query="""SELECT a.product_type_name, COUNT(*) AS purchase_count,
       COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS purchase_share
FROM hm.transactions t JOIN hm.articles a ON t.article_id = a.article_id
WHERE a.index_group_name = 'Ladieswear' GROUP BY a.product_type_name ORDER BY purchase_count DESC LIMIT 10""",
       purpose="H&M 여성(Ladieswear) 상품 유형별 구매 건수 Top 10과 구매 비중", data_sources=["hm"],
       scope_note="Ladieswear 부문 기준(Divided 라인 제외). 비중의 분모는 Ladieswear 전체 구매 건수")])
case("11_zero_rows_final", "존재하지 않는 조건 조회",
     DataQueryPlan(sql_query="""SELECT a.product_type_name, COUNT(*) AS purchase_count
FROM hm.transactions t JOIN hm.articles a ON t.article_id = a.article_id
WHERE a.department_name = 'Ladies' GROUP BY 1""", purpose="항상 0행", data_sources=["hm"]))

graph = get_general_analysis_graph()
from interfaces.slack.delivery import deliver_to_slack

class FakeSlack:
    def __init__(self): self.msgs=[]; self.files=[]
    def chat_postMessage(self, **k): self.msgs.append(k.get("text") or json.dumps(k.get("blocks"))[:200]); return {"ok": True, "ts": "1.0"}
    def files_upload_v2(self, **k): self.files.append(k.get("file") or k.get("filename")); return {"ok": True}

summary = []
for c in CASES:
    data_agent = FakeAgent(c["data"])
    nodes.get_data_agent = lambda cfg=None, a=data_agent: a
    nodes.get_prep_agent = lambda cfg=None, p=c["prep"]: FakeAgent(p)
    nodes.get_visualization_agent = lambda cfg=None, p=c["viz"]: FakeAgent(p)
    try:
        r = graph.invoke({"messages":[HumanMessage(content=c["q"])], "llm_config": {"provider":"stub","model":"stub"}, "completed_steps": []}, config={"recursion_limit": 30})
        slack = FakeSlack()
        d = None
        try:
            d = deliver_to_slack(result=r, client=slack, channel_id="C1", base_dir=PROJECT)
        except Exception as e:
            d = {"delivery_error": repr(e)}
        df = r.get("dataframe")
        summary.append({
            "case": c["name"], "steps": r.get("completed_steps"), "error": r.get("error"),
            "rows": None if df is None else len(df), "cols": None if df is None else list(df.columns)[:6],
            "result_type": r.get("data_result_type"), "viz": (r.get("visualization_result") or {}).get("file_path"),
            "slack_msgs": len(slack.msgs), "slack_files": [Path(str(f)).name for f in slack.files],
            "answer_head": (r.get("final_answer") or "")[:260].replace("\n"," | "),
            "data_agent_calls": len(data_agent.inputs), "retry_count": r.get("data_retry_count", 0),
            "head": None if df is None else df.head(3).to_dict("records"),
            "delivery_keys": sorted(list(d.keys()))[:8] if isinstance(d, dict) else str(type(d)),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        summary.append({"case": c["name"], "EXCEPTION": repr(e)})
print("\n\n==== SUMMARY ====")
for s in summary: print(json.dumps(s, ensure_ascii=False, default=str))
