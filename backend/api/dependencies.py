"""의존성 주입"""
from database.operations.connection import get_operations_session


# DB 세션 의존성 제공
def get_db():
    yield from get_operations_session()


# 에이전트(Orchestrator) 호출 서비스 의존성 제공
def get_chat_service():
    from backend.services.chat_service import get_chat_service as _get

    return _get()
