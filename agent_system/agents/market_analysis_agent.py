"""① Market Analysis Agent — 시장 분석 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 시장 관심도와 외부 상품 리뷰를 분석하여 기획전 방향 및 콘텐츠 근거 제공
메인 활용: 카테고리 선정, 키워드 평가, 경쟁 상품 소구점 분석
서브 활용: 퍼플 트렌드 근거 확인, 리뷰 기반 촬영 포인트 선정

흐름
    run(request) → LLM(Tool calling, langchain create_agent) ⇄ agent_system/tools/market_tools.py
                 → 최종 답변 + 코드가 붙이는 '근거 출처·데이터 상태' (LLM이 빠뜨려도 항상 표시)
- Tool 계산은 shared_analysis/market (백엔드 연결 후에는 backend API가 같은 계산을 수행)
- LLM은 Tool 선택과 설명만 합니다.

출력 (orchestrator/registry.py 공통 결과 형식)
    final_answer, dataframe(요약 표 1개), data_sources, data_purpose, data_scope_note, error
    market_result: Tool별 결과·근거·키워드 — 다른 Agent에 넘길 임시 형식 (Agent 간 데이터 형식 확정 후 변경)
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool, tool as tool_decorator

from agent_system.common_tools.tool_call_log import log_tool_summary, run_logged_tool
from agent_system.prompts.market_analysis_prompt import MARKET_ANALYSIS_PROMPT
from agent_system.tools import market_tools as T

AGENT_NAME = "market_analysis"
RECURSION_LIMIT = 20
MAX_ROWS_FOR_LLM = 40       # Tool 결과를 LLM에 넘길 때 목록 최대 행 수

# Slack 표·CSV로 보낼 표 우선순위 (요약 표 우선, 원시 추이 시계열은 마지막)
TABLE_PRIORITY = ("analyze_trend_sustainability", "analyze_youtube_trends", "analyze_competitor_reviews",
                  "analyze_selling_points", "get_shopping_trends", "search_fashion_trends")
TOOL_TABLE_TITLES = {"analyze_trend_sustainability": "네이버 관심 상승률·지속 근거",
                     "analyze_youtube_trends": "유튜브 반응 (보조 근거)",
                     "analyze_competitor_reviews": "무신사 유사 상품 리뷰 항목 (언급률·불만 비율)",
                     "analyze_selling_points": "무신사 유사 상품 소구점 (소재·핏·관리·디자인)",
                     "get_shopping_trends": "네이버 검색 추이", "search_fashion_trends": "패션 매체 근거"}
MAX_EXTRA_TABLES = 3        # 보고서에 같이 넣을 다른 Tool 표 수 (대표 표 1개 + 최대 3개)
NESTED_COLUMNS = {"top_videos", "top_values", "recent_values", "request", "related_candidates", "evidence_links"}   # 표에서 뺄 중첩 값


# =====================================================================
# Tool 준비
# =====================================================================
def _json_for_llm(result: dict) -> str:
    """LLM에 넘길 Tool 결과 (긴 목록은 줄임)."""
    payload = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("rows", "complaint_terms", "top_similar_products", "items", "series"):
        values = data.get(key)
        if isinstance(values, list) and len(values) > MAX_ROWS_FOR_LLM:
            data[key] = values[:MAX_ROWS_FOR_LLM] + [{"note": f"이하 {len(values) - MAX_ROWS_FOR_LLM}행 생략"}]
    return json.dumps(payload, ensure_ascii=False, default=str)



# CART_SIGNAL_MARKET_DATE_GUARD_V1
def _explicit_date_range(question: str):
    """질문에 명시된 조회 시작일과 종료일을 추출합니다."""
    import re
    from datetime import date

    pattern = (
        r"(20\\d{2})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일"
        r"\\s*부터\\s*(?:(20\\d{2})년\\s*)?"
        r"(\\d{1,2})월\\s*(\\d{1,2})일\\s*까지"
    )
    match = re.search(pattern, question)
    if not match:
        return None

    year, month, day, end_year, end_month, end_day = match.groups()
    start = date(int(year), int(month), int(day))
    end = date(int(end_year or year), int(end_month), int(end_day))

    if start > end:
        raise ValueError("조회 시작일이 종료일보다 늦습니다.")

    return start.isoformat(), end.isoformat()


def build_tools(calls: list[dict], fixed_date_range=None) -> list[StructuredTool]:
    """이번 요청 전용 Tool 목록. 호출 기록을 calls에 남깁니다."""

    def wrap(name, func):
        def runner(**kwargs):
            if name == "get_shopping_trends" and fixed_date_range:
                kwargs["start_date"], kwargs["end_date"] = fixed_date_range
                print(
                    "[시장 분석 날짜 보호] 사용자 지정 기간:",
                    fixed_date_range,
                    flush=True,
                )
            # 공통 Tool 로그: 호출 인자·결과(성공/사용 불가/오류)·시간 (agent_system/common_tools/tool_call_log.py)
            result, record = run_logged_tool(AGENT_NAME, name, func, kwargs)
            calls.append(record)
            return _json_for_llm(result)

        schema = tool_decorator(func).args_schema
        return StructuredTool.from_function(func=runner, name=name, description=(func.__doc__ or "").strip(),
                                            args_schema=schema)

    return [wrap(name, func) for name, func in T.MARKET_TOOLS.items()]


def get_market_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def build_system_prompt(campaign: dict | None, today: date | None = None) -> str:
    values = (campaign or {}).get("values") or {}
    campaign_text = ", ".join(f"{k}={v}" for k, v in values.items()) or "없음"
    status = "; ".join(f"{k}: {v}" for k, v in T.data_status().items())
    return (MARKET_ANALYSIS_PROMPT.replace("@@TODAY@@", str(today or date.today()))
            .replace("@@CAMPAIGN@@", campaign_text).replace("@@STATUS@@", status))


# =====================================================================
# 결과 정리
# =====================================================================
def _data(call: dict) -> dict:
    data = call["result"].get("data")
    return data if isinstance(data, dict) else {}


def _basis_date(call: dict) -> str:
    data = _data(call)
    if data.get("collected_at"):
        return str(data["collected_at"])
    for row in data.get("rows") or []:
        if isinstance(row, dict) and (row.get("latest_period") or row.get("as_of")):
            return str(row.get("latest_period") or row.get("as_of"))
    return ""


def evidence_lines(calls: list[dict]) -> list[str]:
    lines = []
    for call in calls:
        result = call["result"]
        source = result.get("source") or call["name"]
        if result.get("available"):
            basis = _basis_date(call)
            line = f"- {source}: 기준일 {basis}" if basis else f"- {source}"
        else:
            line = f"- {source}: {result.get('status', '사용 불가')}"
        if line not in lines:
            lines.append(line)
    return lines


def _table(call: dict) -> pd.DataFrame | None:
    data = _data(call)
    if call["name"] == "get_shopping_trends" and data.get("series"):
        series = pd.DataFrame(data["series"])
        table = series.pivot_table(index="period", columns="keyword", values="ratio").reset_index()
        table.columns.name = None
        return table
    rows = [{k: v for k, v in row.items() if k not in NESTED_COLUMNS}
            for row in data.get("rows") or data.get("items") or [] if isinstance(row, dict)]
    return pd.DataFrame(rows) if rows else None


def _tables(calls: list[dict]) -> list[tuple[str, pd.DataFrame]]:
    """(Tool 이름, 표) — TABLE_PRIORITY 순서, Tool마다 마지막 호출 1개."""
    out = []
    for name in TABLE_PRIORITY:
        for call in reversed(calls):
            if call["name"] == name and call["result"].get("available"):
                table = _table(call)
                if table is not None and not table.empty:
                    out.append((name, table))
                    break
    return out


def pick_table(calls: list[dict]) -> pd.DataFrame | None:
    tables = _tables(calls)
    return tables[0][1] if tables else None


def extra_tables(calls: list[dict]) -> list[dict]:
    """대표 표 외의 Tool 표 (보고서에 함께 넣음, analysis_log.make_entry의 extra_tables)."""
    return [{"title": TOOL_TABLE_TITLES.get(name, name), "dataframe": table}
            for name, table in _tables(calls)[1:1 + MAX_EXTRA_TABLES]]


def market_result(calls: list[dict]) -> dict:
    out = {"keywords": [], "tools": [], "evidence": []}
    for call in calls:
        result = call["result"]
        out["tools"].append({"name": call["name"], "args": call["args"], "available": bool(result.get("available")),
                             "status": result.get("status", "")})
        for word in call["args"].get("keywords") or []:
            if word not in out["keywords"]:
                out["keywords"].append(word)
        out["evidence"].extend(result.get("evidence") or [])
        if result.get("available"):
            out[call["name"]] = _data(call)
    return out


# =====================================================================
# 실행
# =====================================================================
def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    calls: list[dict] = []
    try:
        agent = create_agent(
            model=get_market_model(request.llm_config),
            tools=build_tools(calls, _explicit_date_range(request.question)),
            system_prompt=build_system_prompt(getattr(request, "campaign", None)),
        )
        user_input = request.question

        if getattr(request, "is_followup", False):
            previous = getattr(request, "last_analysis", None) or {}

            if previous:
                # 직전 분석 요약을 후속 질문의 참고 정보로 전달
                context = {
                    "previous_question": previous.get("question", ""),
                    "previous_purpose": previous.get("purpose", ""),
                    "previous_columns": previous.get("columns", []),
                    "previous_row_count": previous.get("row_count", 0),
                    "previous_preview": str(previous.get("preview", ""))[:3000],
                    "previous_scope": previous.get("scope_note", ""),
                }

                user_input = (
                    "[직전 시장 분석 요약]\n"
                    + json.dumps(context, ensure_ascii=False, default=str)
                    + "\n[이번 사용자 요청]\n"
                    + request.question
                    + "\n미리보기에 없는 수치는 추정하지 말고 도구로 다시 조회하세요."
                )

        output = agent.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"recursion_limit": RECURSION_LIMIT},
        )
        answer = next((m.content for m in reversed(output.get("messages", []))
                       if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None)), "")
        if isinstance(answer, list):  # 일부 모델은 content를 블록 목록으로 반환
            answer = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in answer)
        if not str(answer).strip():
            raise ValueError("LLM이 최종 답변을 만들지 않았습니다.")
    except Exception as exc:
        log_tool_summary(AGENT_NAME, calls)
        print(f"[시장 분석 Agent 실패] {type(exc).__name__}: {exc}")
        return {
            "final_answer": "시장 분석을 완료하지 못했습니다. 잠시 후 다시 요청해 주세요.",
            "error": f"{type(exc).__name__}: {exc}",
            "data_sources": sorted({T.TOOL_SOURCE[c["name"]] for c in calls}),
            "market_result": market_result(calls),
        }

    # LLM이 Tool을 동시에 부르면 끝나는 순서가 달라지므로 Tool 목록 순서로 정렬 (표시를 항상 같게)
    order = list(T.MARKET_TOOLS)
    calls.sort(key=lambda c: order.index(c["name"]))
    log_tool_summary(AGENT_NAME, calls)
    final = str(answer).strip()
    lines = evidence_lines(calls)
    if lines:
        final += "\n\n---\n근거 출처·데이터 상태\n" + "\n".join(lines)
    used = list(dict.fromkeys(c["name"] for c in calls))
    result = {
        "final_answer": final,
        "data_sources": sorted({T.TOOL_SOURCE[name] for name in used}),
        "data_purpose": "시장 분석: " + (", ".join(used) or "Tool 호출 없음"),
        "data_scope_note": "외부 데이터(네이버·유튜브·무신사·패션 매체) 기준",
        "market_result": market_result(calls),
        "completed_steps": ["market_analysis"],
    }
    tables = _tables(calls)
    if tables:
        result["dataframe"] = tables[0][1]
        result["table_title"] = TOOL_TABLE_TITLES.get(tables[0][0], tables[0][0])
        result["extra_tables"] = extra_tables(calls)
    return result


if __name__ == "__main__":
    # LLM 없이 Tool만 실행해 지금 데이터로 확인:
    #   python -m agent_system.agents.market_analysis_agent 하프집업 니트
    import sys

    keyword = " ".join(sys.argv[1:]) or "니트"
    print("데이터 연결 상태:", json.dumps(T.data_status(), ensure_ascii=False, indent=2))
    for name, func in T.MARKET_TOOLS.items():
        result = func(query=keyword) if name == "search_fashion_trends" else func(keywords=[keyword])
        print(f"\n===== {name} =====")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])
