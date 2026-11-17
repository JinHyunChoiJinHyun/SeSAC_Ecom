"""에이전트 대화 API (Orchestrator 멀티턴)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api.dependencies import get_chat_service
from backend.schemas.chat import ChatRequest, ChatResponse, ConversationDetail
from backend.services.chat_service import ChatService, ServiceError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


def _fail(exc: ServiceError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, service: ChatService = Depends(get_chat_service)):
    """대화 한 턴. 같은 thread_id(또는 user_id)로 보내면 이전 대화를 이어서 처리합니다.
    (sync 함수 → FastAPI가 스레드 풀에서 실행하므로 LLM·DB 호출이 이벤트 루프를 막지 않음)"""
    try:
        return service.chat(request)
    except ServiceError as exc:
        _fail(exc)
    except Exception as exc:
        if type(exc).__name__ == "ModelSettingsError":   # OPENAI_API_KEY 없음 등 (env/.env 확인)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        logger.exception("에이전트 처리 실패")
        raise HTTPException(status_code=500, detail=f"처리 중 오류가 발생했습니다: {type(exc).__name__}") from exc


@router.get("/conversations/{thread_id}", response_model=ConversationDetail)
def conversation(thread_id: str, service: ChatService = Depends(get_chat_service)):
    """저장된 대화 기억 (메시지·직전 분석 요약)."""
    try:
        return service.conversation(thread_id)
    except ServiceError as exc:
        _fail(exc)


@router.delete("/conversations/{thread_id}")
def delete_conversation(thread_id: str, service: ChatService = Depends(get_chat_service)):
    """대화 기억 삭제 → 다음 메시지부터 새 대화."""
    try:
        service.delete_conversation(thread_id)
    except ServiceError as exc:
        _fail(exc)
    return {"deleted": thread_id}


@router.get("/files/{kind}/{file_name}")
def download(kind: str, file_name: str, service: ChatService = Depends(get_chat_service)):
    """결과 그래프(png)·표(csv) 내려받기."""
    try:
        path = service.resolve_file(kind, file_name)
    except ServiceError as exc:
        _fail(exc)
    return FileResponse(path, media_type="text/csv" if kind == "csv" else "image/png", filename=path.name)
