"""서버, PostgreSQL 및 Agent 상태 확인 API"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.api.dependencies import get_chat_service, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
def health_check():
    """서버 응답 상태 확인"""
    return {"api": "ok"}


@router.get("/db")
def database_check(db: Session = Depends(get_db)):
    """PostgreSQL 연결 상태 확인"""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("PostgreSQL 연결 확인 실패")
        raise HTTPException(
            status_code=503,
            detail={"api": "ok", "database": "disconnected"},
        )

    return {"api": "ok", "database": "connected"}


@router.get("/agent")
def agent_check(service=Depends(get_chat_service)):
    """멀티턴 Agent 서비스 상태 확인"""
    try:
        status = service.agent_status()
    except Exception:
        logger.exception("Agent 상태 확인 실패")
        raise HTTPException(
            status_code=503,
            detail={"api": "ok", "agent": "error"},
        )

    if status.get("agent") != "ok":
        raise HTTPException(status_code=503, detail=status)

    return {"api": "ok", **status}
