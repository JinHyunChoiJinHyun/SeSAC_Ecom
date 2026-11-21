"""전문 Agent 공통 실행 (아키텍처 agent_system/base_agent.py): 프롬프트 구성, Tool 호출 루프, 결과 형식.

③ 타깃 선정 · ④ 콘텐츠 생성 · ⑤ 검증 · ⑥ 결과 정리 · ⑦ 캠페인 실행 Agent가 이 함수를 씁니다.
(① 시장 분석·② 상품 분석은 먼저 만들어 각자 같은 흐름을 갖고 있습니다. 나중에 이 파일로 합칠 수 있습니다.)

흐름 run_tool_agent(config, request, get_model)
    1. 준비 확인: Tool이 모두 '미구현'(백엔드·데이터·모델 연결 전)이면 LLM을 부르지 않고 미구현 안내를 돌려줌
       → 결과에 not_ready=True (워크플로는 이 단계에서 멈춤, 연결 후 '워크플로 계속')
    2. LLM(langchain create_agent)이 Tool 선택 — 모든 Tool은 run_logged_tool로 실행·로그 (팀 규칙)
       실행 Tool(config.guarded_tools: 메일·발송 등)은 request.approved_actions에 있을 때만 호출
    3. 최종 답변 + 코드가 붙이는 '근거 출처·데이터 상태', 요약 표, <name>_result(다른 Agent용 임시 형식)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pandas as pd
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool, tool as tool_decorator

from agent_system.common_tools.tool_call_log import log_tool_summary, run_logged_tool

RECURSION_LIMIT = 20
MAX_ROWS_FOR_LLM = 40
FOLLOWUP_PREVIEW_CHARS = 3000


@dataclass(frozen=True)
class ToolAgentConfig:
    name: str                                   # registry 이름 (예: targeting)
    label: str                                  # 한글 이름 (예: 타깃 선정)
    tools: dict                                 # {Tool 이름: 함수}  (설계안 이름 그대로)
    tool_source: dict                           # {Tool 이름: 출처 key}
    prompt: str                                 # 시스템 프롬프트 (@@TODAY@@ @@CAMPAIGN@@ @@STATUS@@)
    data_status: Callable[[], dict]             # 출처별 연결 상태
    is_ready: Callable[[], bool]                # False면 LLM 없이 미구현 안내
    scope_note: str = ""
    table_priority: tuple = ()                  # 표로 보낼 Tool 우선순위
    guarded_tools: frozenset = field(default_factory=frozenset)


def _json_for_llm(result: dict) -> str:
    payload = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("rows", "items"):
        values = data.get(key)
        if isinstance(values, list) and len(values) > MAX_ROWS_FOR_LLM:
            data[key] = values[:MAX_ROWS_FOR_LLM] + [{"note": f"이하 {len(values) - MAX_ROWS_FOR_LLM}행 생략"}]
    return json.dumps(payload, ensure_ascii=False, default=str)


def guarded_result(tool: str) -> dict:
    return {"available": False, "status": "실행 보류: 사람의 명시적 승인 기록이 없습니다 (채팅 답은 승인이 아님)",
            "source": tool, "data": {}, "evidence": [], "note": "이 Tool은 승인 없이 실행하지 않습니다. 실행했다고 말하지 마십시오."}


def build_tools(config: ToolAgentConfig, calls: list[dict], approved: tuple = ()) -> list[StructuredTool]:
    def wrap(name, func):
        guarded = name in config.guarded_tools and name not in set(approved or ())

        def runner(**kwargs):
            target = (lambda **_: guarded_result(name)) if guarded else func
            result, record = run_logged_tool(config.name, name, target, kwargs)
            calls.append(record)
            return _json_for_llm(result)

        schema = tool_decorator(func).args_schema
        description = (func.__doc__ or "").strip() + (" [실행 Tool: 승인된 경우에만 실행]" if name in config.guarded_tools else "")
        return StructuredTool.from_function(func=runner, name=name, description=description, args_schema=schema)

    return [wrap(name, func) for name, func in config.tools.items()]


def build_system_prompt(config: ToolAgentConfig, campaign: dict | None, today: date | None = None) -> str:
    values = (campaign or {}).get("values") or {}
    campaign_text = ", ".join(f"{k}={v}" for k, v in values.items()) or "없음"
    status = "; ".join(f"{k}: {v}" for k, v in config.data_status().items())
    return (config.prompt.replace("@@TODAY@@", str(today or date.today()))
            .replace("@@CAMPAIGN@@", campaign_text).replace("@@STATUS@@", status))


def build_user_input(request) -> str:
    question = request.question
    previous = getattr(request, "last_analysis", None) or {}
    if not getattr(request, "is_followup", False) or not previous:
        return question
    context = {
        "previous_question": previous.get("question", ""),
        "previous_purpose": previous.get("purpose", ""),
        "previous_columns": previous.get("columns", []),
        "previous_preview": str(previous.get("preview", ""))[:FOLLOWUP_PREVIEW_CHARS],
    }
    return ("[직전 분석 요약]\n" + json.dumps(context, ensure_ascii=False, default=str)
            + "\n\n[이번 사용자 요청]\n" + question
            + "\n\n직전 결과에 없는 값은 추정하지 말고 Tool로 다시 조회하십시오.")


def not_ready_result(config: ToolAgentConfig) -> dict:
    """모든 Tool이 미구현일 때: LLM을 부르지 않고 연결 상태만 안내."""
    status = "\n".join(f"- {k}: {v}" for k, v in config.data_status().items())
    tools = ", ".join(config.tools)
    return {
        "final_answer": (f"{config.label} Agent는 아직 미구현 상태입니다. 결과를 추정해서 만들지 않습니다.\n"
                         f"{status}\n- Tool: {tools}\n연결되면 같은 요청으로 다시 실행하거나, 워크플로에서는 [다시 시도] 버튼을 눌러 주세요."),
        "not_ready": True,
        "data_sources": [],
        "data_purpose": f"{config.label}: 미구현",
        "data_scope_note": config.scope_note,
        f"{config.name}_result": {"tools": [], "not_ready": True},
        "completed_steps": [],
    }


def _evidence_lines(calls: list[dict]) -> list[str]:
    lines = []
    for call in calls:
        result = call["result"]
        source = result.get("source") or call["name"]
        line = f"- {source}" if result.get("available") else f"- {source}: {result.get('status', '사용 불가')}"
        if line not in lines:
            lines.append(line)
    return lines


def _pick_table(config: ToolAgentConfig, calls: list[dict]) -> pd.DataFrame | None:
    for name in config.table_priority or tuple(config.tools):
        for call in reversed(calls):
            data = call["result"].get("data")
            if call["name"] == name and call["result"].get("available") and isinstance(data, dict):
                rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
                if rows:
                    return pd.DataFrame(rows)
    return None


def _agent_result(config: ToolAgentConfig, calls: list[dict]) -> dict:
    out = {"tools": [], "evidence": []}
    for call in calls:
        result = call["result"]
        out["tools"].append({"name": call["name"], "args": call["args"], "available": bool(result.get("available")),
                             "status": result.get("status", "")})
        out["evidence"].extend(result.get("evidence") or [])
        if result.get("available"):
            out[call["name"]] = result.get("data")
    return out


def run_tool_agent(config: ToolAgentConfig, request, get_model: Callable) -> dict:
    """registry에서 부르는 진입점의 공통 구현: run(AgentRequest) -> dict"""
    if not config.is_ready():
        print(f"[{config.label} Agent] 미구현 — Tool 연결 전이라 LLM을 호출하지 않습니다.")
        return not_ready_result(config)

    calls: list[dict] = []
    try:
        agent = create_agent(model=get_model(request.llm_config),
                             tools=build_tools(config, calls, getattr(request, "approved_actions", ())),
                             system_prompt=build_system_prompt(config, getattr(request, "campaign", None)))
        output = agent.invoke({"messages": [HumanMessage(content=build_user_input(request))]},
                              config={"recursion_limit": RECURSION_LIMIT})
        answer = next((m.content for m in reversed(output.get("messages", []))
                       if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None)), "")
        if isinstance(answer, list):
            answer = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in answer)
        if not str(answer).strip():
            raise ValueError("LLM이 최종 답변을 만들지 않았습니다.")
    except Exception as exc:
        log_tool_summary(config.name, calls)
        print(f"[{config.label} Agent 실패] {type(exc).__name__}: {exc}")
        return {"final_answer": f"{config.label}을(를) 완료하지 못했습니다. 잠시 후 다시 요청해 주세요.",
                "error": f"{type(exc).__name__}: {exc}",
                "data_sources": sorted({config.tool_source[c["name"]] for c in calls}),
                f"{config.name}_result": _agent_result(config, calls)}

    order = list(config.tools)
    calls.sort(key=lambda c: order.index(c["name"]))
    log_tool_summary(config.name, calls)
    final = str(answer).strip()
    lines = _evidence_lines(calls)
    if lines:
        final += "\n\n---\n근거 출처·데이터 상태\n" + "\n".join(lines)
    used = list(dict.fromkeys(c["name"] for c in calls))
    result = {
        "final_answer": final,
        "data_sources": sorted({config.tool_source[name] for name in used}),
        "data_purpose": f"{config.label}: " + (", ".join(used) or "Tool 호출 없음"),
        "data_scope_note": config.scope_note,
        f"{config.name}_result": _agent_result(config, calls),
        "completed_steps": [config.name],
    }
    # 호출한 Tool이 모두 미구현이었으면 워크플로가 다음 단계로 넘어가지 않도록 표시
    if calls and all(c["result"].get("not_ready") for c in calls):
        result["not_ready"] = True
    table = _pick_table(config, calls)
    if table is not None:
        result["dataframe"] = table
    return result
