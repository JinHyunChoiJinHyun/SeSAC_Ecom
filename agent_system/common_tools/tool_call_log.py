"""특화 Agent 공통: Tool 호출 로그 (어떤 Tool을 어떤 인자로 불렀고 결과가 어땠는지).

모든 특화 Agent는 Tool을 실행할 때 run_logged_tool()을 거칩니다 (팀 규칙).
로그는 print로 남기며, env/logger.py가 run_id와 함께 logs/app.log와 터미널에 기록합니다.

    [Tool 호출] market_analysis → analyze_competitor_reviews {"keywords": ["하프집업 니트"]}
    [Tool 결과] market_analysis → analyze_competitor_reviews | 성공 | 연결됨 (로컬 샘플 CSV) | rows=4, evidence=1 | 0.12s
    [Tool 요약] market_analysis | 3회 호출: 성공 2 · 사용 불가 1 | analyze_trend_sustainability(사용 불가), …

결과 판정 (ToolResult 형식 {"available", "status", "data", "evidence"} 기준)
    성공       available=True
    사용 불가  available=False (미연결·백엔드 오류 등, 숫자 없음)
    오류       Tool 실행 중 예외 (결과는 available=False로 바꿔 Agent에 전달)
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

MAX_ARGS_CHARS = 300


def _short_json(value: Any, limit: int = MAX_ARGS_CHARS) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _size(result: dict) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    parts = []
    for key in ("rows", "items", "series"):
        if isinstance(data.get(key), list):
            parts.append(f"{key}={len(data[key])}")
    if isinstance(result.get("evidence"), list):
        parts.append(f"evidence={len(result['evidence'])}")
    return ", ".join(parts) or "-"


def run_logged_tool(agent: str, tool: str, func: Callable[..., dict], kwargs: dict) -> tuple[dict, dict]:
    """Tool을 실행하고 호출·결과 로그를 남깁니다. 예외는 사용 불가 결과로 바꿉니다.

    반환: (result, record)  record = {"agent", "name", "args", "result", "outcome", "elapsed_sec"}
    """
    print(f"[Tool 호출] {agent} → {tool} {_short_json(kwargs)}")
    start = time.perf_counter()
    try:
        result = func(**kwargs)
        if not isinstance(result, dict):
            result = {"available": True, "status": "", "data": {"value": result}, "evidence": []}
        outcome = "성공" if result.get("available") else "사용 불가"
    except Exception as exc:  # Tool 오류는 Agent를 멈추지 않고 결과로 전달
        result = {"available": False, "status": f"Tool 오류: {type(exc).__name__}: {exc}",
                  "source": tool, "data": {}, "evidence": []}
        outcome = "오류"
    elapsed = time.perf_counter() - start
    print(f"[Tool 결과] {agent} → {tool} | {outcome} | {result.get('status') or '-'} | {_size(result)} | {elapsed:.2f}s")
    record = {"agent": agent, "name": tool, "args": kwargs, "result": result, "outcome": outcome,
              "elapsed_sec": round(elapsed, 3)}
    return result, record


def log_tool_summary(agent: str, records: list[dict]) -> None:
    """Agent 실행이 끝날 때 Tool 호출 요약 한 줄."""
    if not records:
        print(f"[Tool 요약] {agent} | Tool 호출 없음")
        return
    counts = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    detail = " · ".join(f"{k} {v}" for k, v in counts.items())
    names = ", ".join(f"{r['name']}({r['outcome']})" for r in records)
    print(f"[Tool 요약] {agent} | {len(records)}회 호출: {detail} | {names}")
