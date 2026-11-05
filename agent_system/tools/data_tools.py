import re
from langchain.tools import tool
from env.db_client import get_duckdb_connection


# =========================================================
# 내부 함수
# =========================================================

def get_raw_table_names():
    """
    DuckDB raw schema의 테이블 목록을 반환합니다.
    """

    con = get_duckdb_connection(
        read_only=True
    )

    if con is None:
        return set()

    try:

        rows = con.execute(
            """
            SELECT
                table_name

            FROM information_schema.tables

            WHERE table_schema = 'raw'

            ORDER BY table_name
            """
        ).fetchall()

        tables = {
            row[0]
            for row in rows
        }

    except Exception as e:

        print(
            f"[테이블 목록 조회 오류] {repr(e)}"
        )

        tables = set()

    finally:

        con.close()

    return tables


# =========================================================
# Data Agent Tool
# =========================================================

@tool
def list_tables_tool() -> str:
    """
    DuckDB raw schema에 존재하는
    테이블 목록을 조회합니다.
    """

    tables = sorted(
        get_raw_table_names()
    )

    if not tables:

        return (
            "테이블을 조회하지 못했거나 "
            "raw schema에 테이블이 없습니다."
        )

    return "\n".join(
        f"- raw.{table}"
        for table in tables
    )


@tool
def describe_table_tool(
    table_name: str
) -> str:
    """
    지정한 DuckDB 테이블의
    컬럼명과 데이터 타입을 조회합니다.

    Args:
        table_name:
            예: product_buy
            예: raw.product_buy
    """

    allowed_tables = (
        get_raw_table_names()
    )

    clean_name = (
        table_name
        .replace("raw.", "")
        .strip()
    )

    if clean_name not in allowed_tables:

        return (
            f"존재하지 않거나 허용되지 않은 "
            f"테이블입니다: {table_name}"
        )

    con = get_duckdb_connection(
        read_only=True
    )

    if con is None:

        return (
            "DuckDB 연결에 실패했습니다."
        )

    try:

        df = con.execute(
            f"""
            DESCRIBE raw.{clean_name}
            """
        ).fetchdf()

        result = df.to_string(
            index=False
        )

    except Exception as e:

        result = (
            "테이블 구조 조회 오류:\n"
            f"{repr(e)}"
        )

    finally:

        con.close()

    return result


# =========================================================
# SQL 검증
# =========================================================

def validate_select_sql(
    sql: str
):
    """
    Data Agent가 생성한 SQL이
    조회 전용 SQL인지 검사합니다.
    """

    if not sql:

        return (
            False,
            "SQL이 비어 있습니다."
        )

    query = sql.strip()


    # SQL 주석 제거
    query_check = re.sub(
        r"--.*?$",
        " ",
        query,
        flags=re.MULTILINE
    )

    query_check = re.sub(
        r"/\*.*?\*/",
        " ",
        query_check,
        flags=re.DOTALL
    )


    upper_query = (
        query_check
        .strip()
        .upper()
    )


    # SELECT / WITH만 허용
    if not (
        upper_query.startswith("SELECT")
        or upper_query.startswith("WITH")
    ):

        return (
            False,
            "조회용 SELECT 또는 WITH SQL만 허용됩니다."
        )


    # 위험 SQL
    blocked_keywords = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "COPY",
        "ATTACH",
        "DETACH",
        "INSTALL",
        "LOAD",
        "EXPORT",
        "IMPORT",
        "PRAGMA",
        "CALL",
        "READ_PARQUET",
        "READ_CSV",
    ]


    for keyword in blocked_keywords:

        if re.search(
            rf"\b{keyword}\b",
            upper_query
        ):

            return (
                False,
                f"허용되지 않은 SQL 키워드입니다: "
                f"{keyword}"
            )


    # SQL 여러 개 실행 방지
    without_last_semicolon = (
        query_check
        .strip()
        .rstrip(";")
    )

    if ";" in without_last_semicolon:

        return (
            False,
            "한 번에 하나의 SQL만 실행할 수 있습니다."
        )


    return (
        True,
        "SQL 검증을 통과했습니다."
    )


# =========================================================
# SQL Executor
# =========================================================

def execute_sql_to_dataframe(
    sql: str
):
    """
    DuckDB에서 SQL을 실행하고
    pandas DataFrame을 반환합니다.
    """

    is_valid, message = (
        validate_select_sql(sql)
    )


    if not is_valid:

        return (
            None,
            message
        )


    con = get_duckdb_connection(
        read_only=True
    )


    if con is None:

        return (
            None,
            "DuckDB 연결에 실패했습니다."
        )


    try:

        df = con.execute(
            sql
        ).fetchdf()

        error = None


    except Exception as e:

        df = None

        error = (
            f"SQL 실행 오류: {repr(e)}"
        )


    finally:

        con.close()


    return (
        df,
        error
    )