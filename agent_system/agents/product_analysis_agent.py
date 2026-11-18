"""② Product Analysis Agent — 상품 분석 에이전트 (「CART_SIGNAL 에이전트 및 Tool 설계안」)

역할: 자사 상품 정보와 구매 기록을 분석하여 적합한 상품 선정
메인 활용: 트렌드에 적합한 H&M 주력 상품 추천 (메인 시나리오 5·8단계)
서브 활용: 브라운·네이비 후보 구매 기록 비교 및 첫 화면 상품 결정 (서브 시나리오 ②)

흐름
    run(request) → LLM(Tool calling, langchain create_agent) ⇄ agent_system/tools/product_tools.py
                 → 최종 답변 + 코드가 붙이는 '근거 출처·데이터 상태' (LLM이 빠뜨려도 항상 표시)
- Tool 계산은 shared_analysis/product (백엔드 연결 후에는 backend /products/* API가 같은 계산을 수행)
- LLM은 Tool 선택과 설명만 합니다.
- Tool 호출은 공통 로그(agent_system/common_tools/tool_call_log.py)로 남깁니다.

출력 (orchestrator/registry.py 공통 결과 형식)
    final_answer, dataframe(요약 표 1개), data_sources, data_purpose, data_scope_note, error
    product_result: Tool별 결과·근거·상품 ID — 다른 Agent에 넘길 임시 형식 (Agent 간 데이터 형식 확정 후 변경)
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool, tool as tool_decorator

from agent_system.common_tools.tool_call_log import log_tool_summary, run_logged_tool
from agent_system.prompts.product_analysis_prompt import PRODUCT_ANALYSIS_PROMPT
from agent_system.tools import product_tools as T

AGENT_NAME = "product_analysis"
RECURSION_LIMIT = 20
MAX_ROWS_FOR_LLM = 40       # Tool 결과를 LLM에 넘길 때 목록 최대 행 수

# Slack 표·CSV로 보낼 표 우선순위 (요약 표 우선, 원시 추이 시계열은 마지막)
TABLE_PRIORITY = ("compare_product_candidates", "analyze_product_sales", "match_product_keywords",
                  "get_product_details", "search_products")
NESTED_COLUMNS = {"weekly", "matched_parts", "other_colours", "full_match_keywords"}   # 표에서 뺄 중첩 값
LIST_COLUMNS = {"style_tags", "materials", "design_features"}                         # 표에는 쉼표로 이어 붙임


# =====================================================================
# Tool 준비
# =====================================================================
def _json_for_llm(result: dict) -> str:
    """LLM에 넘길 Tool 결과 (긴 목록은 줄임)."""
    payload = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("rows",):
        values = data.get(key)
        if isinstance(values, list) and len(values) > MAX_ROWS_FOR_LLM:
            data[key] = values[:MAX_ROWS_FOR_LLM] + [{"note": f"이하 {len(values) - MAX_ROWS_FOR_LLM}행 생략"}]
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_tools(calls: list[dict]) -> list[StructuredTool]:
    """이번 요청 전용 Tool 목록. 호출 기록을 calls에 남깁니다."""

    def wrap(name, func):
        def runner(**kwargs):
            # 공통 Tool 로그: 호출 인자·결과(성공/사용 불가/오류)·시간 (agent_system/common_tools/tool_call_log.py)
            result, record = run_logged_tool(AGENT_NAME, name, func, kwargs)
            calls.append(record)
            return _json_for_llm(result)

        schema = tool_decorator(func).args_schema
        return StructuredTool.from_function(func=runner, name=name, description=(func.__doc__ or "").strip(),
                                            args_schema=schema)

    return [wrap(name, func) for name, func in T.PRODUCT_TOOLS.items()]


def get_product_model(llm_config: dict | None = None):
    """요청에 고정된 채팅 모델 (테스트에서 바꿔 끼울 수 있게 함수로 둠)."""
    from env.gpt_client import get_openai_chat_client
    return get_openai_chat_client(model_config=llm_config)


def build_system_prompt(campaign: dict | None, today: date | None = None) -> str:
    values = (campaign or {}).get("values") or {}
    campaign_text = ", ".join(f"{k}={v}" for k, v in values.items()) or "없음"
    status = "; ".join(f"{k}: {v}" for k, v in T.data_status().items())
    return (PRODUCT_ANALYSIS_PROMPT.replace("@@TODAY@@", str(today or date.today()))
            .replace("@@CAMPAIGN@@", campaign_text).replace("@@STATUS@@", status))


# =====================================================================
# 결과 정리
# =====================================================================
def _data(call: dict) -> dict:
    data = call["result"].get("data")
    return data if isinstance(data, dict) else {}


def _basis_date(call: dict) -> str:
    data = _data(call)
    period = data.get("period") or {}
    if period.get("start"):
        return f"{period['start']}~{period['end']} {data.get('channel', '')}".strip()
    return ""


def evidence_lines(calls: list[dict]) -> list[str]:
    lines = []
    for call in calls:
        result = call["result"]
        source = result.get("source") or call["name"]
        if result.get("available"):
            basis = _basis_date(call)
            line = f"- {source}: 기간 {basis}" if basis else f"- {source}"
        else:
            line = f"- {source}: {result.get('status', '사용 불가')}"
        if line not in lines:
            lines.append(line)
    return lines


def _table(call: dict) -> pd.DataFrame | None:
    data = _data(call)
    rows = [{k: (", ".join(map(str, v)) if k in LIST_COLUMNS and isinstance(v, list) else v)
             for k, v in row.items() if k not in NESTED_COLUMNS}
            for row in data.get("rows") or [] if isinstance(row, dict)]
    return pd.DataFrame(rows) if rows else None


def pick_table(calls: list[dict]) -> pd.DataFrame | None:
    for name in TABLE_PRIORITY:
        for call in reversed(calls):
            if call["name"] == name and call["result"].get("available"):
                table = _table(call)
                if table is not None and not table.empty:
                    return table
    return None


def product_result(calls: list[dict]) -> dict:
    out = {"keywords": [], "article_ids": [], "tools": [], "evidence": []}
    for call in calls:
        result = call["result"]
        out["tools"].append({"name": call["name"], "args": call["args"], "available": bool(result.get("available")),
                             "status": result.get("status", "")})
        for word in call["args"].get("keywords") or []:
            if word not in out["keywords"]:
                out["keywords"].append(word)
        for article_id in call["args"].get("article_ids") or []:
            if article_id not in out["article_ids"]:
                out["article_ids"].append(article_id)
        out["evidence"].extend(result.get("evidence") or [])
        if result.get("available"):
            out[call["name"]] = _data(call)
    return out


# =====================================================================
# 실행
# =====================================================================
FOLLOWUP_PREVIEW_CHARS = 3000   # 직전 분석 미리보기(표 앞부분)를 LLM에 넘기는 최대 글자 수


def build_user_input(request) -> str:
    """후속 질문이면 직전 분석 요약(표 미리보기 포함)을 함께 넘깁니다.

    "그 후보들 4주 구매 비교해줘"처럼 앞 결과의 상품 ID를 가리키는 질문에 쓰입니다.
    직전 분석은 같은 Agent일 때만 Orchestrator가 채웁니다 (registry AgentRequest.last_analysis).
    """
    question = request.question
    previous = getattr(request, "last_analysis", None) or {}
    if not getattr(request, "is_followup", False) or not previous:
        return question
    context = {
        "previous_question": previous.get("question", ""),
        "previous_purpose": previous.get("purpose", ""),
        "previous_columns": previous.get("columns", []),
        "previous_row_count": previous.get("row_count", 0),
        "previous_preview": str(previous.get("preview", ""))[:FOLLOWUP_PREVIEW_CHARS],
        "previous_scope": previous.get("scope_note", ""),
    }
    return (
        "[직전 상품 분석 요약]\n"
        + json.dumps(context, ensure_ascii=False, default=str)
        + "\n\n[이번 사용자 요청]\n"
        + question
        + "\n\n직전 결과의 상품 ID(article_id)는 미리보기에 있는 값만 쓰십시오. "
          "미리보기에 없는 상품·수치는 추정하지 말고 Tool로 다시 조회하십시오."
    )


def run(request) -> dict:
    """registry에서 부르는 진입점: run(AgentRequest) -> dict"""
    calls: list[dict] = []
    try:
        agent = create_agent(
            model=get_product_model(request.llm_config),
            tools=build_tools(calls),
            system_prompt=build_system_prompt(getattr(request, "campaign", None)),
        )
        output = agent.invoke({"messages": [HumanMessage(content=build_user_input(request))]},
                              config={"recursion_limit": RECURSION_LIMIT})
        answer = next((m.content for m in reversed(output.get("messages", []))
                       if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None)), "")
        if isinstance(answer, list):  # 일부 모델은 content를 블록 목록으로 반환
            answer = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in answer)
        if not str(answer).strip():
            raise ValueError("LLM이 최종 답변을 만들지 않았습니다.")
    except Exception as exc:
        log_tool_summary(AGENT_NAME, calls)
        print(f"[상품 분석 Agent 실패] {type(exc).__name__}: {exc}")
        return {
            "final_answer": "상품 분석을 완료하지 못했습니다. 잠시 후 다시 요청해 주세요.",
            "error": f"{type(exc).__name__}: {exc}",
            "data_sources": sorted({T.TOOL_SOURCE[c["name"]] for c in calls}),
            "product_result": product_result(calls),
        }

    # LLM이 Tool을 동시에 부르면 끝나는 순서가 달라지므로 Tool 목록 순서로 정렬 (표시를 항상 같게)
    order = list(T.PRODUCT_TOOLS)
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
        "data_purpose": "상품 분석: " + (", ".join(used) or "Tool 호출 없음"),
        "data_scope_note": "H&M 자사 상품·구매 기록 기준 (기간은 데이터 마지막 날 기준)",
        "product_result": product_result(calls),
        "completed_steps": ["product_analysis"],
    }
    table = pick_table(calls)
    if table is not None:
        result["dataframe"] = table
    return result


if __name__ == "__main__":
    # LLM 없이 Tool만 실행해 지금 데이터로 확인:
    #   python -m agent_system.agents.product_analysis_agent 하프집업 니트
    import sys

    keyword = " ".join(sys.argv[1:]) or "하프집업 니트"
    print("데이터 연결 상태:", json.dumps(T.data_status(), ensure_ascii=False, indent=2))
    found = T.search_products(query=keyword, limit=5)
    ids = [r["article_id"] for r in found["data"].get("rows", [])][:3]
    checks = {
        "search_products": found,
        "match_product_keywords": T.match_product_keywords(keywords=[keyword], top_n=3),
        "get_product_details": T.get_product_details(article_ids=ids),
        "analyze_product_sales": T.analyze_product_sales(article_ids=ids),
        "compare_product_candidates": T.compare_product_candidates(article_ids=ids, keywords=[keyword]),
    }
    for name, result in checks.items():
        print(f"\n===== {name} =====")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])
