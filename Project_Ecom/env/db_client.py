from pathlib import Path
import duckdb


# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# DuckDB 파일 경로
DB_PATH = PROJECT_ROOT / "database" / "synerise.duckdb"


# DuckDB 연결 객체 생성 함수
def get_duckdb_connection(read_only=True):

    try:
        # database 폴더가 없으면 생성
        DB_PATH.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # 조회 전용인데 DB 파일이 없으면 오류 발생
        if read_only and not DB_PATH.exists():
            raise FileNotFoundError(
                f"DuckDB 파일이 없습니다: {DB_PATH}\n"
                "먼저 db_setup.py를 실행해주세요."
            )

        client = duckdb.connect(
            database=str(DB_PATH),
            read_only=read_only
        )

    except Exception as e:
        print(f"[DuckDB 연결 오류] {repr(e)}")
        client = None

    return client