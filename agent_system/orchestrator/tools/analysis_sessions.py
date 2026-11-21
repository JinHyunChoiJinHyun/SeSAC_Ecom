"""Slack 분석 세션 선택기. 기존 LangGraph 체크포인트를 삭제하지 않습니다."""
from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "runtime" / "analysis_sessions.sqlite"
_LOCK = threading.RLock()
_COMMAND = re.compile(r"^\s*새\s*분석\s*시작(?:\s*[:：]\s*(.*)|\s+(.+))?\s*$", re.S)


def parse_start_command(text: str) -> str | None:
    """명시적 시작 명령만 인정. None=일반 질문, ''=새 세션만 시작."""
    match = _COMMAND.fullmatch(str(text or ""))
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("CREATE TABLE IF NOT EXISTS active_sessions (base_thread_id TEXT PRIMARY KEY, active_thread_id TEXT NOT NULL)")
    return conn


def current(base_thread_id: str) -> str:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT active_thread_id FROM active_sessions WHERE base_thread_id=?", (base_thread_id,)).fetchone()
        return row[0] if row else base_thread_id


def start(base_thread_id: str) -> str:
    """기존 이력 보존, 이후 메시지를 새 세션으로 이동."""
    with _LOCK, _connect() as conn:
        new_id = f"{base_thread_id}:analysis:{uuid.uuid4().hex}"
        conn.execute("INSERT INTO active_sessions (base_thread_id, active_thread_id) VALUES (?, ?) "
                     "ON CONFLICT(base_thread_id) DO UPDATE SET active_thread_id=excluded.active_thread_id",
                     (base_thread_id, new_id))
        return new_id


def switch_allowed(graph, thread_id: str) -> tuple[bool, str]:
    """승인·워크플로 진행 중 새 세션 전환을 차단합니다."""
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return False, "기존 대화 상태를 확인하지 못해 새 세션을 시작하지 않았습니다."
    if getattr(snapshot, "interrupts", ()) or any(getattr(t, "interrupts", ()) for t in getattr(snapshot, "tasks", ())):
        return False, "승인 대기 중입니다. 승인·반려·취소를 먼저 처리해 주세요."
    values = dict(getattr(snapshot, "values", {}) or {})
    if values.get("pending_approval"):
        return False, "승인 대기 중입니다. 승인·반려·취소를 먼저 처리해 주세요."
    run = values.get("workflow_run") or {}
    status = run.get("status", "") if isinstance(run, dict) else ""
    if status in {"running", "waiting_input", "waiting_approval", "paused", "blocked"}:
        return False, "진행 중인 기획전 워크플로가 있습니다. 먼저 완료하거나 취소해 주세요."
    return True, ""
