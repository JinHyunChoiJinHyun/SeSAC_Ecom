"""
database/analytics/connection.py

분석 DB(DuckDB, H&M만) 연결 — 조회 전용
backend·스크립트·노트북·테스트 모두 이 파일로 연다. 이 파일에는 쓰기 연결이 없다.

DuckDB에 쓰는 건 H&M 원천 적재(최초 1회)와 피처 재생성뿐이고, 둘 다 수동으로 실행한다.
그래서 별도 잠금 조율(PostgreSQL advisory lock)은 두지 않고 DuckDB 파일 잠금에 맡긴다.

DuckDB 파일 잠금 (프로세스 단위)
  - 읽기 전용 연결은 여러 프로세스가 동시에 열 수 있다.
  - 쓰기 연결이 열려 있으면 읽기 전용 연결을 열 수 없고, 그 반대도 마찬가지다.
  → 적재 중에 들어온 조회는 AnalyticsDBUpdatingError (backend가 503으로 변환)
  → 조회 중에 적재를 시작하면 적재 스크립트가 잠금 오류로 실패 (데이터는 그대로)
  운영 규칙: 적재·피처 재생성은 backend를 내린 상태에서 실행한다.

요청마다 열고 닫는다
  연결을 오래 붙잡지 않아야 backend가 한가할 때 적재 스크립트가 파일을 열 수 있다.

설정 (환경변수)
  ANALYTICS_DB_PATH       기본: <프로젝트 루트>/data/analytics.duckdb
  DUCKDB_MEMORY_LIMIT     예: "4GB"  (미설정 시 DuckDB 기본값)
  DUCKDB_THREADS          예: "4"    (미설정 시 DuckDB 기본값)
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analytics.duckdb"

# --------------------------------------------------
# 예외 (backend/api/error_handlers.py가 HTTP 응답으로 변환)
# --------------------------------------------------

class AnalyticsDBError(RuntimeError):
    """분석 DB 연결 관련 오류"""

class AnalyticsDBUpdatingError(AnalyticsDBError):
    """적재 중이라 조회 불가 -> 503 에러"""

    def __init__(self, message: str = "분석 데이터 갱신 중입니다. 잠시 후 다시 시도하세요"):
        super().__init__(message)

class AnalyticsDBNotFoundError(AnalyticsDBError):
    """DuckDB 파일이 없음"""

# --------------------------------------------------
# 설정
# --------------------------------------------------

def get_db_path() -> Path:
    return Path(os.getenv("ANALYTICS_DB_PATH", str(DEFAULT_DB_PATH))).resolve()

def _duckdb_config() -> dict[str, str]:
    config: dict[str, str] = {}

    if memory_limit := os.getenv("DUCKDB_MEMORY_LIMIT"):
        config["memory_limit"] = memory_limit
    if threads := os.getenv("DUCKDB_THREADS"):
        config["threads"] = threads
    return config

# --------------------------------------------------
# 조회 연결
# --------------------------------------------------

def connect_readonly(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """읽기 전용 열기"""
    db_path = Path(path).resolve() if path else get_db_path()

    if not db_path.exists():
        raise AnalyticsDBNotFoundError(
            f"분석 DB 파일이 없습니다: {db_path}\n"
            f"H&M 원천 적재(batch/load_hm_data.py)를 먼저 실행하세요."
        )

    try:
        return duckdb.connect(str(db_path), read_only=True, config=_duckdb_config())
    except duckdb.IOException as e:
        # 적재 스크립트가 쓰기 모드로 사용하고 있는 경우
        if "lock" in str(e).lower():
            raise AnalyticsDBUpdatingError() from e
        raise

@contextmanager
def analytics_connection(path: Path | str | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """with 블록 생성"""

    con = connect_readonly(path)
    try:
        yield con
    finally:
        con.close()

# --------------------------------------------------
# health test
# --------------------------------------------------
def is_analytic_db_updating(path: Path | str | None = None) -> bool:
    """읽기 전용으로 열고 바로 닫기"""
    try:
        connect_readonly(path).close()
        return False
    except AnalyticsDBUpdatingError:
        return True