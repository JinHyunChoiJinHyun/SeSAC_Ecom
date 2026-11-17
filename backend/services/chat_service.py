"""에이전트(Orchestrator) 호출 서비스 — FastAPI 엔드포인트와 에이전트 코드 사이의 연결부.

Slack 봇(slack_bot.py)과 같은 Orchestrator 그래프·대화 기억(LangGraph Checkpointer)을 사용합니다.
    POST /chat 한 번 = run_turn() 한 번
        Orchestrator(대화 판단·후속 질문 재작성·라우팅) → 특화 Agent(현재 General Analysis) → 결과 JSON

동시 처리
    - 그래프 생성(matplotlib)이 스레드 안전하지 않아 한 번에 1턴만 처리 (CART_SIGNAL_MAX_CONCURRENT, 기본 1)
    - 같은 대화(thread_id)의 요청은 순서대로 처리
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


from backend.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationMessage,
    FilePayload,
    TablePayload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _file_folders():
    from agent_system.common_tools.execution_tools.output_tools import CSV_OUTPUT_DIR

    return {"png": PROJECT_ROOT / "visualization_output", "csv": CSV_OUTPUT_DIR}


FILE_FOLDERS = _file_folders()
RESPONSE_ROWS = int(os.getenv("CART_SIGNAL_RESPONSE_ROWS", "100"))


class ServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ChatService:

    def __init__(self, graph=None):
        self._graph = graph
        self._slots = threading.BoundedSemaphore(int(os.getenv("CART_SIGNAL_MAX_CONCURRENT", "1")))
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @property
    def graph(self):
        if self._graph is None:
            from agent_system.orchestrator.graph import get_graph

            self._graph = get_graph()
        return self._graph

    def _lock(self, thread_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(thread_id, threading.Lock())

    # ------------------------------------------------------------------ 대화 한 턴
    def chat(self, request: ChatRequest) -> ChatResponse:
        from agent_system.orchestrator.graph import run_turn
        from env.logger import request_log
        from env.model_settings import model_request

        thread_id = request.thread_id or f"api:{request.user_id}"
        started = time.perf_counter()
        with request_log(request.message), model_request() as config:
            if not self._slots.acquire(timeout=600):
                raise ServiceError("처리 대기 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", 503)
            try:
                with self._lock(thread_id):
                    result = run_turn(
                        question=request.message,
                        thread_id=thread_id,
                        llm_config=config.public(),
                        graph=self.graph,
                        metadata={"source": "api", "user_id": request.user_id},
                    )
            finally:
                self._slots.release()
        return self.build_response(result, thread_id, time.perf_counter() - started)

    # ------------------------------------------------------------------ 결과 → JSON
    @staticmethod
    def _dataframe(result: dict):
        from agent_system.common_tools.execution_tools.output_tools import select_result_dataframe

        return select_result_dataframe(result)[0]

    def build_response(self, result: dict, thread_id: str, elapsed: float) -> ChatResponse:
        df = self._dataframe(result)
        files = []
        visualization = result.get("visualization_result") if isinstance(result.get("visualization_result"), dict) else {}
        png = result.get("visualization_path") or visualization.get("file_path")
        if png and Path(str(png)).is_file():
            files.append(self._file("png", Path(str(png))))
        if df is not None and not result.get("error"):
            # 공통 Tool: Slack Delivery와 같은 규칙으로 결과 CSV 생성 (0행·대용량은 생략)
            from agent_system.common_tools.execution_tools.output_tools import prepare_result_csv

            csv_path = prepare_result_csv(result)["path"]
            if csv_path is not None:
                files.append(self._file("csv", Path(csv_path)))

        return ChatResponse(
            thread_id=thread_id,
            turn_type=str(result.get("turn_type") or "analysis"),
            answer=str(result.get("final_answer") or ""),
            route=str(result.get("route") or ""),
            standalone_question=str(result.get("standalone_question") or ""),
            is_followup=bool(result.get("is_followup")),
            conversation_reset=bool(result.get("conversation_reset")),
            sql=str(result.get("sql_query") or ""),
            data_sources=[str(s) for s in (result.get("data_sources") or [])],
            table=self._table(df),
            files=files,
            error=str(result.get("error") or ""),
            awaiting_approval=bool(result.get("awaiting_approval") or result.get("turn_type") == "approval_request"),
            campaign=result.get("campaign") if isinstance(result.get("campaign"), dict) else {},
            elapsed_sec=round(elapsed, 3),
        )

    @staticmethod
    def _file(kind: str, path: Path) -> FilePayload:
        return FilePayload(kind=kind, file_name=path.name, url=f"/files/{kind}/{path.name}")

    @staticmethod
    def _table(df) -> TablePayload | None:
        if df is None:
            return None
        limited = df.head(RESPONSE_ROWS)
        payload = json.loads(limited.to_json(orient="split", date_format="iso", default_handler=str, index=False))
        return TablePayload(columns=[str(c) for c in payload.get("columns", [])], rows=payload.get("data", []),
                            total_rows=int(len(df)), truncated=len(df) > len(limited))

    # ------------------------------------------------------------------ 대화 기억 조회·삭제
    def conversation(self, thread_id: str) -> ConversationDetail:
        from langchain_core.messages import AIMessage, HumanMessage

        values = self.graph.get_state({"configurable": {"thread_id": thread_id}}).values
        if not values:
            raise ServiceError(f"대화를 찾지 못했습니다: {thread_id}", 404)
        messages = [
            ConversationMessage(
                role="user" if isinstance(m, HumanMessage) else "assistant" if isinstance(m, AIMessage) else "other",
                name=getattr(m, "name", "") or "", content=str(m.content))
            for m in values.get("messages", [])
        ]
        return ConversationDetail(thread_id=thread_id, last_activity_at=str(values.get("last_activity_at", "")),
                                  messages=messages, last_analysis=values.get("last_analysis") or {})

    def delete_conversation(self, thread_id: str):
        if not self.graph.get_state({"configurable": {"thread_id": thread_id}}).values:
            raise ServiceError(f"대화를 찾지 못했습니다: {thread_id}", 404)
        self.graph.checkpointer.delete_thread(thread_id)

    # ------------------------------------------------------------------ 결과 파일
    @staticmethod
    def resolve_file(kind: str, file_name: str) -> Path:
        folder = FILE_FOLDERS.get(kind)
        if folder is None or Path(file_name).name != file_name or not file_name.endswith(f".{kind}"):
            raise ServiceError("잘못된 파일 요청입니다.", 400)
        path = (folder / file_name).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            raise ServiceError("파일을 찾지 못했습니다.", 404)
        return path

    # ------------------------------------------------------------------ 에이전트 상태
    @staticmethod
    def agent_status() -> dict:
        from agent_system.orchestrator.registry import get_agents
        from agent_system.orchestrator.tools.memory import MEMORY_DB_PATH
        from common.data_sources import get_sources

        sources = {s.key: s.is_available() for s in get_sources()}
        required_ok = all(s.is_available() for s in get_sources() if s.required)
        return {
            "agent": "ok" if required_ok else "degraded",
            "data_sources": sources,
            "agents": [spec.name for spec in get_agents()],
            "memory_db": str(MEMORY_DB_PATH),
        }


_service: ChatService | None = None
_service_guard = threading.Lock()


def get_chat_service() -> ChatService:
    global _service
    with _service_guard:
        if _service is None:
            _service = ChatService()
        return _service
