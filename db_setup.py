from pathlib import Path

from env.db_client import get_duckdb_connection


# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parent

# Synerise 원본 데이터 폴더
DATA_DIR = PROJECT_ROOT / "synerise_dataset"


# Parquet 파일명 : DuckDB 테이블명
PARQUET_TABLES = {
    "product_buy.parquet": "product_buy",
    "add_to_cart.parquet": "add_to_cart",
    "remove_from_cart.parquet": "remove_from_cart",
    "page_visit.parquet": "page_visit",
    "search_query.parquet": "search_query",
    "product_properties.parquet": "product_properties",
}

# duckdb 생성, 이파일은 처음 한번만 실행
def setup_database():

    # DB 생성/수정이 필요하므로 read_only=False
    con = get_duckdb_connection(
        read_only=False
    )

    # DB 연결 실패
    if con is None:
        print("[ERROR] DuckDB 연결에 실패했습니다.")
        return

    try:
        # raw schema 생성
        con.execute("""
            CREATE SCHEMA IF NOT EXISTS raw
        """)

        for file_name, table_name in PARQUET_TABLES.items():

            file_path = DATA_DIR / file_name

            # 파일이 없으면 건너뜀
            if not file_path.exists():
                print(f"[파일 없음] {file_path}")
                continue

            print(f"[저장 시작] {file_name}")

            # DuckDB SQL에서 사용할 경로
            sql_path = (
                file_path
                .as_posix()
                .replace("'", "''")
            )

            # Parquet 데이터를 DuckDB Table로 저장
            con.execute(f"""
                CREATE OR REPLACE TABLE
                    raw.{table_name}
                AS

                SELECT *
                FROM read_parquet(
                    '{sql_path}'
                )
            """)

            # 저장된 행 개수 확인
            row_count = con.execute(f"""
                SELECT COUNT(*)
                FROM raw.{table_name}
            """).fetchone()[0]

            print(
                f"[완료] raw.{table_name}"
                f" → {row_count:,} rows"
            )

    except Exception as e:
        print(
            f"[DB 설정 오류] {repr(e)}"
        )

    finally:
        con.close()


if __name__ == "__main__":
    setup_database()