import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])

@router.get("")
def health_check():
    """서버 응답 확인"""
    return {"api": "ok"}

@router.get("/db")
def database_check(db: Session = Depends(get_db)):
    """PostgreSQL 연결 확인"""
    try:
        db.execute(text("SELECT 1"))

    except SQLAlchemyError:
        logger.exception("PostgreSQL 연결 확인 실패")

        raise HTTPException(
            status_code=503,
            detail={
                "api": "ok",
                "database": "disconnected"
            }
        )
    return {"api": "ok", "database": "connected"}