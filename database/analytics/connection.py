"""분석 DB (DuckDB) 읽기 전용 연결"""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from contextlib import contextmanager
from collections.abc import Iterator

import duckdb
from database.operations.connection import operations_engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env") # 지정된 경로의 env 파일 로드

ANALYTICS_LOCK_ID = 7_310_001 # 잠금 식별 번호

class AnalyticsDBUpdatingError(RuntimeError):
    """적재 중인 경우 분석 DB 조회 불가"""

def analytic_db_path() -> Path:
    """DuckDB 파일 경로"""
    if raw_path := os.getenv("ANALYTIC_DB_PATH"):
        path = Path(raw_path).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    return PROJECT_ROOT / "data" / "analytics.duckdb"

@contextmanager
def analytic_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """읽기 전용 연결"""
    lock_connection = None
    db_connection = None
    lock_acquired = False

    try:
        # 1. 잠금을 위해 PostgreSQL 연결
        lock_connection = operations_engine.connect()

        # 2. DuckDB 공유 잠금 요청
        lock_acquired = lock_connection.execute(
            text("SELECT pg_try_advisory_lock_shared(:lock_id)"),
            {"lock_id": ANALYTICS_LOCK_ID}
        )