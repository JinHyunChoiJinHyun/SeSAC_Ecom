import os

import pytest
from sqlalchemy import text # SQL 문자열을 SQLAlchemy가 SQL 쿼리로 처리할 수 있도록 변환하는 함수

from database.operations.connection import get_engine

@pytest.mark.db
def test_db_connection():
    """PostgreSQL 연결 및 SQL 실행 테스트"""
    if not (os.getenv("OPS_DATABASE_URL") or all(os.getenv(k) for k in ("DB_USER", "DB_PASSWORD", "DB_NAME"))):
        pytest.skip("DB_USER·DB_PASSWORD·DB_NAME(.env)이 없어 PostgreSQL 연결 테스트를 건너뜁니다.")
    with get_engine().connect() as connection:
        result = connection.execute(
            text(
                """
                    SELECT
                        current_database(),
                        current_user,
                        now()
                """
            )
        ).fetchone()

        assert result is not None

        assert result[0] == os.environ["DB_NAME"]
        assert result[1] == os.environ["DB_USER"]