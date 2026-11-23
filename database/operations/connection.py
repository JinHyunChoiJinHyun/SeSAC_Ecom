"""운영 DB (PostgresSQL) 연결"""
import os

from pathlib import Path
from contextlib import contextmanager # 함수를 with문으로 사용할 수 있도록 변경
from functools import lru_cache # 함수 결과를 메모리에 저장 (캐시) -> 동일 함수 재호출 시 저장된 결과 재사용

from dotenv import load_dotenv

from sqlalchemy import Engine, create_engine # DB 연결을 관리하는 엔진 클래스와 엔진 생성 도구
from sqlalchemy.engine import URL # DB 접속 정보 담는 URL 객체
from sqlalchemy.orm import Session, sessionmaker # DB 작업 및 트랜잭션을 관리하는 세션 클래스와 생성 도구

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 프로젝트 루트의 환경변수 파일을 순서대로 불러오기
# 이미 설정된 환경변수는 덮어쓰지 않음
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "env" / ".env", override=False)

def get_database_url() -> str | URL:
    """DB URL 생성 또는 기존 URL 반환"""

    # url이 있으면 반환 없으면 생성
    if database_url := os.getenv("OPS_DATABASE_URL"):
        return database_url

    # 필수 env 불러오기
    required_env = ("DB_USER", "DB_PASSWORD", "DB_NAME")

    # 누락된 env 확인
    missing_env = [key for key in required_env if not os.getenv(key)]

    # 누락된 env 존재할 시 예외 발생
    if missing_env:
        raise RuntimeError(f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing_env)}")

    # URL 객체 반환
    return URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "5433")),
        database=os.getenv("DB_NAME")
    )

@lru_cache
def get_engine() -> Engine:
    """엔진 생성 후 캐시에 저장하여 재사용"""
    return create_engine(get_database_url(), pool_pre_ping=True)

@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """공통 설정을 가진 세션 생성기 생성 후 캐시에 저장하여 재사용"""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)

def get_operations_session():
    """FastAPI 요청용 DB Session 제공 (트랜잭션 관리는 Service에서 담당)"""
    session = get_session_factory()()

    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

@contextmanager
def session_scope():
    """배치 및 스크립트용 DB 트랜잭션 관리"""
    session = get_session_factory()() # 세션 생성기로 세션 생성
    try:
        yield session # with 블록에 Session 전달
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def reset_engine() -> None:
    """테스트용 URL 교체 후 엔진 재생성"""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()