"""운영 DB (PostgresSQL) 연결"""
import os

from pathlib import Path
from contextlib import contextmanager # 함수를 with문으로 사용할 수 있도록 변경
from functools import lru_cache # 함수 결과를 메모리에 저장 (캐시) -> 동일 함수 재호출 시 저장된 결과 재사용

from dotenv import load_dotenv

from sqlalchemy import Engine # DB 연결 관리 객체
from sqlalchemy.engine import URL # DB 접속 정보 담는 URL 객체

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

def _database_url() -> str | URL:
    """DB URL 생성 또는 기존 URL 반환"""

    # url이 있으면 반환 없으면 생성
    if url := os.getenv("OPS_DATABASE_URL"):
        return url

    # 필수 env 불러오기
    required_env = {key: os.getenv(key) for key in ("DB_USER", "DB_PASSWORD", "DB_NAME")}

    # 누락된 env 확인
    missing_env = [key for key, value in required_env.items() if not value]

    # 누락된 env 존재할 시 예외 발생
    if missing_env:
        raise RuntimeError(f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing_env)}")

    # URL 객체 반환
    return URL.create(
        "postgresql+psycopg",
        username=required_env["DB_USER"],
        password=required_env["DB_PASSWORD"],
        host=os.getenv("DB_HOST", "localhost"),
        database=required_env["DB_NAME"]
    )

@lru_cache
def get_engine() -> Engine:


