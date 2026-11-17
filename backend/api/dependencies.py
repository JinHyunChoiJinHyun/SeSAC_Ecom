"""FastAPI 의존성 주입"""

from database.operations.connection import get_operations_session


def get_db():
    """PostgreSQL DB 세션 제공"""
    yield from get_operations_session()


def get_chat_service():
    """멀티턴 Agent 서비스 제공"""
    from backend.services.chat_service import get_chat_service as _get
    return _get()
