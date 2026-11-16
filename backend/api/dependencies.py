"""의존성 주입"""
from database.operations.connection import get_operations_session

# DB 세션 의존성 제공
def get_db():
    yield from get_operations_session()