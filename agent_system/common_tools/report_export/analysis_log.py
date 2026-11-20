"""분석 결과 기록 — 보고서의 재료 (대화 상태 analysis_log에 저장).

Agent·워크플로 단계가 끝날 때마다 한 건씩 남깁니다 (orchestrator.py · tools/workflow.py).
    - 텍스트(질문·답변·기준)는 상태(checkpoint)에, 표는 runtime/report_data/<id>.csv에 저장 (git 제외 폴더)
    - Agent가 표를 여러 개 주면(extra_tables: [{"title", "dataframe"}], 예: 시장 분석 Tool별 표) <id>_<n>.csv로 함께 저장 (최대 MAX_EXTRA_TABLES개)
    - 그래프는 visualization_output/의 PNG 경로를 그대로 기록
    - 상품 이미지는 Agent 결과의 product_images(H&M 이미지 폴더 안 실제 파일)만 경로로 기록 (최대 MAX_IMAGES장)
    - 최근 MAX_ENTRIES건만 보관하고, 밀려난 기록의 표 파일은 지웁니다
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "runtime" / "report_data"
MAX_ENTRIES = 10            # 대화당 보고서에 묶을 수 있는 분석 수
SAVE_ROWS = 200             # 표는 앞 200행까지 저장 (보고서에는 그중 앞부분만 표시)
ANSWER_CHARS = 6000
MAX_IMAGES = 6              # 기록 1건에 붙이는 상품 이미지 수
MAX_EXTRA_TABLES = 3        # 기록 1건에 함께 저장하는 추가 표 수


def _clean_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:40] or "x"


def _images(result: dict) -> list[dict]:
    out = []
    for item in result.get("product_images") or []:
        if isinstance(item, dict) and item.get("path") and Path(str(item["path"])).is_file():
            out.append({"article_id": str(item.get("article_id") or ""), "label": str(item.get("label") or ""),
                        "path": str(item["path"])})
    return out[:MAX_IMAGES]


def _save_table(df: pd.DataFrame, name: str) -> tuple[str, int, list[str]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{_clean_id(name)}.csv"
    df.head(SAVE_ROWS).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path), int(len(df)), [str(c) for c in df.columns]


def _extra_tables(result: dict, entry_id: str) -> list[dict]:
    out = []
    for item in result.get("extra_tables") or []:
        df = item.get("dataframe") if isinstance(item, dict) else None
        if isinstance(df, pd.DataFrame) and not df.empty and len(out) < MAX_EXTRA_TABLES:
            path, rows, columns = _save_table(df, f"{entry_id}_{len(out) + 1}")
            out.append({"title": str(item.get("title") or ""), "path": path, "rows": rows, "columns": columns})
    return out


def make_entry(result: dict, *, entry_id: str, question: str, user_question: str, route: str, route_label: str,
               kind: str = "analysis", workflow: dict | None = None) -> dict | None:
    """Agent 결과 1건 → 기록. 오류·미구현 결과는 기록하지 않습니다 (None)."""
    if not isinstance(result, dict) or result.get("error") or result.get("not_ready"):
        return None
    df = result.get("processed_dataframe")
    if not isinstance(df, pd.DataFrame):
        df = result.get("dataframe")
    table_path, total_rows, columns = "", 0, []
    if isinstance(df, pd.DataFrame) and not df.empty:
        table_path, total_rows, columns = _save_table(df, entry_id)
    chart = result.get("visualization_path") or ""
    viz = result.get("visualization_result") if isinstance(result.get("visualization_result"), dict) else {}
    return {
        "id": entry_id,
        "kind": kind,                                   # analysis / workflow_step
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_question": str(user_question or ""),
        "question": str(question or ""),
        "route": route,
        "route_label": route_label,
        "answer": str(result.get("full_answer") or result.get("final_answer") or "")[:ANSWER_CHARS],
        "purpose": str(result.get("data_purpose") or ""),
        "sources": list(result.get("data_sources") or []),
        "scope_note": str(result.get("data_scope_note") or ""),
        "sql": str(result.get("sql_query") or ""),
        "table_path": table_path,
        "table_rows": total_rows,
        "table_columns": columns,
        "table_title": str(result.get("table_title") or ""),
        "extra_tables": _extra_tables(result, entry_id),       # [{"title", "path", "rows", "columns"}]
        "chart_path": str(chart),
        "chart_title": str(viz.get("title") or ""),
        "images": _images(result),                      # [{"article_id", "label", "path"}] 상품 이미지
        "workflow": workflow or {},                     # {"run_id", "workflow", "title", "step_no", "step_title"}
    }


def append(log: list | None, entry: dict | None) -> list:
    """기록 추가 + 오래된 기록 정리 (밀려난 표 파일 삭제)."""
    log = list(log or [])
    if entry:
        log.append(entry)
    dropped, kept = log[:-MAX_ENTRIES], log[-MAX_ENTRIES:]
    for old in dropped:
        remove_files([old])
    return kept


def remove_files(entries: list) -> None:
    for entry in entries or []:
        paths = [entry.get("table_path")] + [t.get("path") for t in entry.get("extra_tables") or []]
        for value in paths:
            path = Path(str(value or ""))
            try:
                if value and path.is_file() and DATA_DIR in path.resolve().parents:
                    path.unlink()
            except OSError:
                pass


def load_table(entry: dict, path: str | None = None) -> pd.DataFrame | None:
    """기록의 대표 표 (path를 주면 그 추가 표)."""
    path = Path(str(path or entry.get("table_path") or ""))
    if not path.is_file():
        return None
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return None
