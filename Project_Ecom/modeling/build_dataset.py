"""
추후 스케쥴러로 배치 자동화
- 매일: 신규 데이터 적재 -> 라벨/특징 갱신, 모델 예측 결과 저장
- 매달: 쌓인 라벨 데이터로 모델 재학습 -> 기존 모델과 비교 후 더 좋은 경우만 교체
"""
import sys
import logging
import time
from pathlib import Path
from config import(
    PROJECT_ROOT
)

# 모듈 경로에 현재 루트 추가
sys.path.insert(0, str(PROJECT_ROOT))

from database.db_client import get_duckdb_connection

logger = logging.getLogger(__name__)

SQL_DIR = PROJECT_ROOT / "database" / "sql"

# SQL 실행 순서 (단계명, SQL 파일명)
SQL_EXECUTION_STEPS = [
    ("장바구니 이벤트 및 예측 기준 시점 테이블 생성", "build_prediction_points.sql"),
    ("라벨 테이블 생성", "build_label.sql"),
    ("특징 테이블 생성", "build_features.sql"),
    ("학습용 테이블 생성", "build_mart.sql"),
]

# MART 테이블 검증용 쿼리
VALIDATION_QUERY = """
    SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT (cart_event_id, hours_after_cart)) AS unique_events,
        COUNT(*) FILTER (WHERE label = 1) AS purchase_count,
        COUNT(*) FILTER (WHERE label = 0) AS non_purchase_count,
        COUNT(*) FILTER (WHERE label IS NULL) AS null_label_count
    FROM mart.purchase_training_data
"""

# 원본 테이블 목록
RAW_TABLES = [
    "add_to_cart",
    "product_buy",
    "remove_from_cart",
    "page_visit",
    "search_query",
]

def execute_sql_file(conn, file_path:Path) -> None:
    """SQL 파일을 읽어 실행"""
    sql = file_path.read_text(encoding="utf-8")

    try:
        conn.execute(sql)
    except Exception as e:
        raise RuntimeError(f"SQL 실행 실패: {file_path.name}") from e

def validate_mart(conn) -> dict:
    """MART 테이블(학습용 테이블) 결과 검사"""
    # 집계 결과 dict로 반환
    mart_summary = conn.execute(VALIDATION_QUERY).fetchdf().iloc[0].to_dict() # 첫 행 반환(전체 집계 결과는 1행)
    mart_summary = {key: int(value) for key, value in mart_summary.items()}

    errors = []

    # 조건 검증
    # 1) 빈 테이블 검증
    if mart_summary["total_rows"] == 0:
        errors.append("MART 테이블이 비어있습니다.")

    # 2) 중복 검증
    duplicate_count = mart_summary["total_rows"] - mart_summary["unique_events"]
    if duplicate_count > 0:
        errors.append(f"(cart_event_id, hours_after_cart) 중복 {duplicate_count}건 발생)")

    # 3) label 유효성 검증 (0/1/NULL 이외의 값)
    invalid_label_count = (
        mart_summary["total_rows"]
        - mart_summary["purchase_count"]
        - mart_summary["non_purchase_count"]
        - mart_summary["null_label_count"]
    )

    if invalid_label_count > 0:
        errors.append(f"label에 허용되지 않은 값 존재 {invalid_label_count}건")

    if errors:
        raise ValueError("MART 테이블 검증 실패:\n- " + "\n- ".join(errors))

    return mart_summary

def build_clean_tables(conn) -> None:
    """원본 테이블의 timestamp를 문자열에서 TIMESTAMP 타입으로 변환"""
    conn.execute("CREATE SCHEMA IF NOT EXISTS clean")

    for table in RAW_TABLES:
        conn.execute(f"""
            CREATE OR REPLACE TABLE clean.{table} AS
            SELECT * REPLACE (TRY_CAST("timestamp" AS TIMESTAMP) AS "timestamp") 
            FROM raw.{table}
            WHERE TRY_CAST("timestamp" AS TIMESTAMP) IS NOT NULL
        """)
        logger.info("%s 생성 완료", table)

def build_dataset() -> dict:
    """장바구니 이벤트, 라벨, 득징, 학습용 테이블 생성"""

    # 파일 존재 여부 확인
    paths = [SQL_DIR / filename for _, filename in SQL_EXECUTION_STEPS] # 경로 생성
    missing_file = [path for path in paths if not path.exists()] # 경로 존재 검증

    if missing_file:
        raise FileNotFoundError(f"SQL 파일이 없습니다: {missing_file}")

    # DB 연결
    conn = get_duckdb_connection(read_only=False)

    try:
        conn.begin()

        build_clean_tables(conn)

        for step_name, file_name in SQL_EXECUTION_STEPS:
            path = SQL_DIR / file_name
            start_time = time.perf_counter() # 시작 시간 (소요 시간 측정)

            execute_sql_file(conn, path)
            logger.info("%s 완료 (%.1fs)",step_name, time.perf_counter() - start_time)

        # MART 테이블 검증 및 요약 반환
        mart_summary = validate_mart(conn) 
        conn.commit()

    except Exception:
        # 테이블 생성 실패 시
        conn.rollback()
        logger.exception("데이터셋 생성 실패 - 변경 사항 되돌림")
        raise

    finally:
        conn.close() # db 연결 종료

    logger.info(
        "MART 생성 완료: 전체 %d행, 구매 %d, 비구매 %d, 라벨 없음 %d",
        mart_summary["total_rows"],
        mart_summary["purchase_count"],
        mart_summary["non_purchase_count"],
        mart_summary["null_label_count"],
    )

    return mart_summary

if __name__ == "__main__":
    # 직접 실행 시에만 로그 설정(import 시 실행하는 파일의 로그 설정에 영향을 주지 않도록)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    build_dataset()
 
