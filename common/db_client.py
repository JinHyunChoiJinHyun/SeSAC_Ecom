"""CART SIGNAL DuckDB 연결.

연결은 두 종류입니다.

1) 분석용 (Agent)          get_analysis_connection() / open_duckdb_connection()
   common/data_sources.py에 등록된 출처(H&M·Amazon …)를 하나의 읽기 전용 연결에서 조회합니다.
2) 모델링용 (modeling/)     get_duckdb_connection(read_only=True|False)
   팀 모델링 코드가 쓰는 원래 함수 이름·동작 그대로입니다.
   database/synerise.duckdb(환경변수 CART_SIGNAL_MODELING_DB로 변경 가능)에 직접 연결하며,
   read_only=False면 테이블을 만들고 씁니다(build_dataset·predict).
   → modeling 폴더를 새 버전으로 바꿔도 이 함수 이름·인자·경로 규칙만 유지하면 그대로 동작합니다.
   → Agent는 이 함수를 쓰지 않습니다(Agent 질문으로 synerise DB가 바뀌는 일 없음).

아래는 분석용 연결 설명입니다.

- 메모리 DB에 출처별 DuckDB 파일을 READ_ONLY로 ATTACH 합니다 (파일은 합치지 않습니다).
- search_path를 지정해 SQL에서는 `hm.transactions`, `amazon.review`처럼
  schema.table 이름만 쓰면 됩니다.
- 원본 DB 파일은 절대 수정하지 않습니다.
- required=True 출처(H&M)는 파일이 없거나 열지 못하면 오류,
  나머지 출처는 파일이 없거나 열지 못하면(수집 중 잠김 등) 건너뛰고 다른 출처 조회는 계속합니다.

경로는 출처별 환경변수로 바꿀 수 있습니다 (예: CART_SIGNAL_HM_DB, CART_SIGNAL_AMAZON_DB).

주의: 수집 스크립트(크롤러·API)가 같은 DuckDB 파일에 쓰는 동안에는 그 출처를 열 수 없습니다.
이때 선택 출처는 그 조회에서만 빠집니다. 빠지지 않게 하려면 수집은 임시 파일에 쓴 뒤
원래 파일과 바꿔치기(os.replace)하는 방식을 사용하십시오.
"""

import logging
import os
from pathlib import Path

import duckdb

from common.data_sources import get_sources

logger = logging.getLogger(__name__)


# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 대용량 집계 시 임시 파일 위치 (프로젝트 폴더 안, DB 폴더 밖)
TEMP_DIR = Path(os.getenv("CART_SIGNAL_DUCKDB_TEMP", PROJECT_ROOT / "logs" / "duckdb_tmp"))


def get_source_file_status() -> dict:
    """등록된 출처별 DB 파일 존재 여부를 반환합니다. 예: {"hm": True, "amazon": False}"""
    return {source.key: source.is_available() for source in get_sources()}


def open_duckdb_connection():
    """등록된 출처를 붙인 읽기 전용 연결과, 이번에 붙이지 못한 출처 목록을 반환합니다.

    반환: (client, skipped)
        skipped = [{"key": ..., "label": ..., "reason": "missing" | "locked_or_error", "error": ...}]

    - 필수 출처(required=True, H&M): 파일이 없거나 붙이기에 실패하면 오류를 냅니다.
    - 선택 출처(Amazon·무신사 등): 파일이 없거나 붙이기에 실패하면
      (예: 수집 스크립트가 쓰는 중이라 잠김) 경고만 남기고 건너뜁니다.
      → 다른 출처 조회는 계속 동작합니다.
    """

    sources = get_sources()

    missing_required = [
        f"{source.label}: {source.path}"
        for source in sources
        if source.required and not source.is_available()
    ]
    if missing_required:
        raise FileNotFoundError(
            "필수 DuckDB 파일이 없습니다: " + ", ".join(missing_required)
        )

    client = None
    skipped = []
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        client = duckdb.connect(
            database=":memory:",
            config={"temp_directory": str(TEMP_DIR)},
        )

        search_path = []
        for source in sources:
            if not source.is_available():
                logger.warning(
                    "%s DuckDB 파일이 없어 연결하지 않습니다: %s", source.label, source.path
                )
                skipped.append({
                    "key": source.key, "label": source.label,
                    "reason": "missing", "error": "",
                })
                continue

            try:
                client.execute(
                    f"ATTACH '{source.path.as_posix()}' AS {source.catalog} (READ_ONLY)"
                )
            except Exception as exc:
                if source.required:
                    raise
                # 선택 출처는 건너뜁니다. 대표적인 원인: 수집 스크립트가 쓰는 중(파일 잠금)
                logger.warning(
                    "%s DuckDB 파일을 열지 못해 이번 조회에서 제외합니다 (%s): %s",
                    source.label, type(exc).__name__, exc,
                )
                skipped.append({
                    "key": source.key, "label": source.label,
                    "reason": "locked_or_error", "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            search_path.append(f"{source.catalog}.{source.db_schema}")
            search_path.extend(
                f"{source.catalog}.{schema}" for schema in source.extra_search_schemas
            )

        # 주의: USE 문을 실행하면 search_path가 바뀌므로 사용하지 않습니다.
        client.execute(f"SET search_path = '{','.join(search_path)}'")

    except Exception:
        # 원인을 로그로 남기고 에러는 상위로 전파
        logger.exception(
            "DuckDB 연결 실패: %s",
            {source.key: str(source.path) for source in sources},
        )
        if client is not None:
            client.close()
        raise

    return client, skipped


def get_analysis_connection():
    """Agent 분석용: 등록된 출처를 함께 조회하는 읽기 전용 DuckDB 연결을 반환합니다.

    원본 DB는 항상 READ_ONLY로 연결합니다.
    선택 출처를 붙이지 못한 경우의 목록이 필요하면 open_duckdb_connection()을 쓰십시오.
    """

    client, _ = open_duckdb_connection()
    return client


# =========================================================
# 모델링용 (modeling/ 폴더) — 팀 코드와 같은 함수 이름·동작
# =========================================================

DEFAULT_MODELING_DB = PROJECT_ROOT / "database" / "synerise.duckdb"


def get_modeling_db_path() -> Path:
    """모델링 DB 경로. 환경변수 CART_SIGNAL_MODELING_DB가 있으면 그 경로를 씁니다."""

    return Path(os.getenv("CART_SIGNAL_MODELING_DB") or DEFAULT_MODELING_DB)


def get_duckdb_connection(read_only=True):
    """모델링 DB(synerise.duckdb)에 직접 연결합니다. (modeling/ 코드 전용)

    - read_only=True : 조회 전용. 파일이 없으면 FileNotFoundError
    - read_only=False: 테이블 생성·저장(build_dataset·predict). 파일이 없으면 새로 만듭니다.
    DuckDB 파일은 쓰기 연결이 열려 있는 동안 다른 프로세스에서 열 수 없습니다.
    """

    db_path = get_modeling_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if read_only and not db_path.exists():
            raise FileNotFoundError(
                f"DuckDB 파일이 없습니다: {db_path}\n"
                "먼저 python run_modeling.py setup 으로 원본 데이터를 적재해 주세요."
            )
        client = duckdb.connect(database=str(db_path), read_only=read_only)
    except Exception:
        logger.exception("모델링 DuckDB 연결 실패: %s (read_only=%s)", db_path, read_only)
        raise

    return client
