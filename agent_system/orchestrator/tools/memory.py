"""Orchestrator 멀티턴 Tool ① — 대화 기억 저장소(LangGraph Checkpointer)와 대화 ID(thread_id) 규칙.

- 기억 저장: LangGraph checkpointer (SQLite 파일, runtime/conversation_memory.sqlite)
  봇을 재시작해도 같은 대화 ID로 이어집니다.
- LangSmith: 기억을 저장하는 곳이 아니라 실행 기록(trace)을 보는 곳입니다.
  run config의 metadata에 thread_id를 넣어 LangSmith Threads 화면에서
  같은 대화의 턴들이 하나로 묶여 보이게 합니다.

대화 ID 규칙
- Slack DM          : dm:<user_id>                         (사용자별로 계속 이어짐)
- Slack 채널 멘션    : settings.CHANNEL_MEMORY_SCOPE / CHANNEL_PER_USER에 따라
                      "channel"(기본) → ch:<channel_id>:<user_id>             (같은 채널이면 새 메시지여도 이어짐)
                      "thread"        → ch:<channel_id>:<thread_ts>:<user_id> (봇 답변 스레드 안에서만 이어짐)
                      CHANNEL_PER_USER=False이면 끝의 :<user_id>가 빠져 참여자 전체가 공유
- 터미널(main.py)   : cli:<이름>
"""

import logging
import os
import sqlite3
from pathlib import Path

LOGGER = logging.getLogger("agent_system.orchestrator.memory")

PROJECT_ROOT = Path(__file__).resolve().parents[3]

MEMORY_DB_PATH = Path(
    os.getenv(
        "CART_SIGNAL_MEMORY_DB",
        PROJECT_ROOT / "runtime" / "conversation_memory.sqlite",
    )
)

_checkpointer = None


def get_checkpointer():
    """대화 기억용 checkpointer를 반환합니다 (프로세스당 1개).

    langgraph-checkpoint-sqlite가 없으면 메모리 저장(InMemorySaver)으로 대체하고
    경고를 남깁니다. 이 경우 봇을 재시작하면 대화 기억이 사라집니다.
    """

    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Slack 봇은 작업 스레드에서 그래프를 실행하므로 check_same_thread=False
        connection = sqlite3.connect(str(MEMORY_DB_PATH), check_same_thread=False)
        _checkpointer = SqliteSaver(connection)
        LOGGER.info("대화 기억 저장소: %s", MEMORY_DB_PATH)

    except ImportError:
        from langgraph.checkpoint.memory import InMemorySaver

        LOGGER.warning(
            "langgraph-checkpoint-sqlite가 설치되지 않아 메모리에만 대화를 기억합니다. "
            "봇을 재시작하면 대화 기억이 사라집니다. "
            "(pip install langgraph-checkpoint-sqlite)"
        )
        _checkpointer = InMemorySaver()

    return _checkpointer


def build_slack_thread_id(event: dict) -> str:
    """Slack 이벤트에서 대화 ID를 만듭니다.

    - DM: 사용자별 (dm:<사용자>)
    - 채널 멘션: settings.CHANNEL_MEMORY_SCOPE
        "channel" (기본): 같은 채널·같은 사용자면 새 메시지로 멘션해도 이어짐
        "thread"        : 같은 스레드 안에서만 이어짐
      settings.CHANNEL_PER_USER=True(기본)이면 사용자마다 따로 기억
    """

    from agent_system.orchestrator import settings as conversation_settings

    user = event.get("user", "unknown")

    if event.get("channel_type") == "im":
        return f"dm:{user}"

    scope = str(getattr(conversation_settings, "CHANNEL_MEMORY_SCOPE", "channel")).lower()
    per_user = getattr(
        conversation_settings,
        "CHANNEL_PER_USER",
        getattr(conversation_settings, "CHANNEL_THREAD_PER_USER", True),  # 이전 설정 이름 호환
    )

    thread_id = f"ch:{event.get('channel', 'unknown')}"
    if scope == "thread":
        thread_ts = event.get("thread_ts") or event.get("ts") or "no_ts"
        thread_id = f"{thread_id}:{thread_ts}"

    if per_user:
        thread_id = f"{thread_id}:{user}"

    return thread_id


def langsmith_enabled() -> bool:
    """LangSmith 추적이 켜져 있는지 확인합니다 (키 값은 읽지 않습니다)."""

    flag = (
        os.getenv("LANGSMITH_TRACING")
        or os.getenv("LANGCHAIN_TRACING_V2")
        or ""
    ).strip().lower()
    has_key = bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY"))
    return flag in {"true", "1", "yes"} and has_key


def prune_thread_history(checkpointer, thread_id: str) -> int:
    """대화 하나의 이전 저장본(checkpoint)을 지우고 최신 상태 1개만 남깁니다.

    LangGraph checkpointer는 실행 단계마다 상태를 새로 저장하고 예전 것을 지우지 않습니다.
    그래서 봇이 읽는 현재 상태에서는 지운 메시지(40개 초과분, 12시간 자동 리셋)도
    예전 저장본에는 남아 있습니다. 이 함수는 그 예전 저장본을 실제로 삭제합니다.

    settings.PRUNE_OLD_CHECKPOINTS = True 일 때 매 턴이 끝나면 호출됩니다.
    반환: 삭제한 저장본 수
    """

    # SQLite 저장소 (langgraph-checkpoint-sqlite)
    connection = getattr(checkpointer, "conn", None)
    if isinstance(connection, sqlite3.Connection):
        lock = getattr(checkpointer, "lock", None)
        if lock is None:
            from contextlib import nullcontext
            lock = nullcontext()
        with lock:
            # 지운 내용이 파일의 빈 공간에 남지 않도록 0으로 덮어씁니다.
            connection.execute("PRAGMA secure_delete = ON")
            # checkpoint_id는 시간순으로 정렬되는 ID입니다 (LangGraph도 이 순서로 최신을 고름).
            latest_condition = """
                checkpoint_id NOT IN (
                    SELECT MAX(checkpoint_id) FROM checkpoints AS latest
                    WHERE latest.thread_id = ? AND latest.checkpoint_ns = checkpoints.checkpoint_ns
                )
            """
            deleted = connection.execute(
                f"DELETE FROM checkpoints WHERE thread_id = ? AND {latest_condition}",
                (thread_id, thread_id),
            ).rowcount
            connection.execute(
                """
                DELETE FROM writes
                WHERE thread_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM checkpoints AS kept
                      WHERE kept.thread_id = writes.thread_id
                        AND kept.checkpoint_ns = writes.checkpoint_ns
                        AND kept.checkpoint_id = writes.checkpoint_id
                  )
                """,
                (thread_id,),
            )
            connection.commit()
        return max(int(deleted or 0), 0)

    # 메모리 저장소 (InMemorySaver: 패키지 미설치 시 대체 저장소)
    storage = getattr(checkpointer, "storage", None)
    if isinstance(storage, dict) and thread_id in storage:
        deleted = 0
        for namespace, checkpoints in storage[thread_id].items():
            if len(checkpoints) <= 1:
                continue
            latest = max(checkpoints)
            for checkpoint_id in [key for key in checkpoints if key != latest]:
                del checkpoints[checkpoint_id]
                getattr(checkpointer, "writes", {}).pop((thread_id, namespace, checkpoint_id), None)
                deleted += 1
        return deleted

    LOGGER.warning("이 checkpointer는 이전 저장본 정리를 지원하지 않습니다: %s", type(checkpointer).__name__)
    return 0
