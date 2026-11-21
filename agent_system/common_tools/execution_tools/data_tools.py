import re

from langchain.tools import tool

from common.data_sources import merged, source_keys
from common.db_client import get_analysis_connection, open_duckdb_connection


def allowed_schemas() -> tuple[str, ...]:
    """조회를 허용하는 schema = 등록되어 있고 DB 파일이 있는 출처 (common/data_sources.py)."""
    return tuple(source_keys(available_only=True))


# 이전 import 호환용 (모듈을 불러올 때의 값)
ALLOWED_SCHEMAS = allowed_schemas()


# =========================================================
# 내부 함수
# =========================================================

def get_table_names():
    """조회 가능한 출처 schema의 테이블·뷰 목록을 'schema.table' 형태로 반환합니다."""

    try:
        con = get_analysis_connection()
    except Exception as e:
        print(f"[DuckDB 연결 오류] {repr(e)}")
        return set()

    try:
        schema_list = ", ".join(f"'{schema}'" for schema in allowed_schemas()) or "''"
        rows = con.execute(
            f"""
            SELECT DISTINCT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ({schema_list})
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        tables = {f"{row[0]}.{row[1]}" for row in rows}

    except Exception as e:
        print(f"[테이블 목록 조회 오류] {repr(e)}")
        tables = set()

    finally:
        con.close()

    return tables


# =========================================================
# Data Agent Tool (필요할 때 Agent에 연결할 수 있도록 유지)
# =========================================================

@tool
def list_tables_tool() -> str:
    """
    조회 가능한 데이터 출처(common/data_sources.py) 테이블 목록을 반환합니다.
    """

    tables = sorted(get_table_names())

    if not tables:
        return "테이블을 조회하지 못했거나 연결된 데이터 출처에 테이블이 없습니다."

    return "\n".join(f"- {table}" for table in tables)


@tool
def describe_table_tool(table_name: str) -> str:
    """
    지정한 테이블의 컬럼명과 데이터 타입을 조회합니다.

    Args:
        table_name: 예) hm.transactions, amazon.review
    """

    clean_name = str(table_name or "").strip().lower()
    allowed_tables = get_table_names()

    if clean_name not in allowed_tables:
        return f"존재하지 않거나 허용되지 않은 테이블입니다: {table_name}"

    try:
        con = get_analysis_connection()
    except Exception as e:
        return f"DuckDB 연결에 실패했습니다: {repr(e)}"

    try:
        df = con.execute(f"DESCRIBE {clean_name}").fetchdf()
        result = df.to_string(index=False)

    except Exception as e:
        result = "테이블 구조 조회 오류:\n" f"{repr(e)}"

    finally:
        con.close()

    return result


# =========================================================
# SQL 검증
# =========================================================

# 데이터 변경·외부 파일 접근·설정 변경을 막습니다.
BLOCKED_KEYWORDS = [
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
    "SET",
    "RESET",
    "USE",
    "CHECKPOINT",
    "VACUUM",
]

# 임의 파일·URL을 읽는 테이블 함수
BLOCKED_FUNCTIONS = [
    "READ_PARQUET",
    "PARQUET_SCAN",
    "READ_CSV",
    "READ_CSV_AUTO",
    "READ_JSON",
    "READ_JSON_AUTO",
    "READ_NDJSON",
    "READ_TEXT",
    "READ_BLOB",
    "GLOB",
    "SNIFF_CSV",
    "QUERY",
    "QUERY_TABLE",
]


def _strip_comments_and_strings(sql: str, preserve_identifiers: bool = False) -> str:
    """SQL 문자열과 주석을 구별하여 검사용 SQL을 생성합니다.

    문자열 내부의 -- 또는 /* */를 주석으로 처리하지 않습니다.
    문자열은 작은따옴표 두 개('')로 치환하여 기존 파일 경로 검사를 유지합니다.
    파싱할 수 없는 문자열이나 주석은 안전하게 거부합니다.
    """
    result = []
    i = 0
    n = len(sql)

    while i < n:
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < n else ""

        # 작은따옴표 문자열 리터럴
        if char == "'":
            i += 1
            closed = False

            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue

                    i += 1
                    closed = True
                    break

                i += 1

            if not closed:
                raise ValueError("닫히지 않은 SQL 문자열입니다.")

            result.append("''")
            continue

        # 큰따옴표로 감싼 식별자
        if char == '"':
            start = i
            i += 1
            closed = False

            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue

                    i += 1
                    closed = True
                    break

                i += 1

            if not closed:
                raise ValueError("닫히지 않은 SQL 식별자입니다.")

            # 식별자 내부도 키워드 검사에서 제외
            result.append(
                sql[start:i] if preserve_identifiers else '""'
            )
            continue

        # 한 줄 주석
        if char == "-" and next_char == "-":
            i += 2

            while i < n and sql[i] not in "\r\n":
                i += 1

            result.append(" ")
            continue

        # 블록 주석
        if char == "/" and next_char == "*":
            i += 2
            depth = 1

            while i < n and depth > 0:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1

            if depth:
                raise ValueError("닫히지 않은 SQL 주석입니다.")

            result.append(" ")
            continue

        result.append(char)
        i += 1

    return "".join(result)



def validate_select_sql(sql: str):
    """
    Data Agent가 생성한 SQL이 조회 전용 SQL인지 검사합니다.
    """

    if not sql:
        return (False, "SQL이 비어 있습니다.")

    try:
        query_check = _strip_comments_and_strings(sql.strip())
    except ValueError as exc:
        return (False, str(exc))
    upper_query = query_check.strip().upper()

    # SELECT / WITH만 허용
    if not (upper_query.startswith("SELECT") or upper_query.startswith("WITH")):
        return (False, "조회용 SELECT 또는 WITH SQL만 허용됩니다.")

    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper_query):
            # USING SAMPLE / SAMPLE 등과 충돌하지 않는 단어만 차단합니다.
            return (False, f"허용되지 않은 SQL 키워드입니다: {keyword}")

    for function_name in BLOCKED_FUNCTIONS:
        if re.search(rf"\b{function_name}\s*\(", upper_query):
            return (False, f"외부 파일을 읽는 함수는 사용할 수 없습니다: {function_name}")

    # 파일 경로를 테이블처럼 읽는 구문 차단 (예: FROM 'file.parquet')
    if re.search(r"\bFROM\s+''", upper_query) or re.search(r"\bJOIN\s+''", upper_query):
        return (False, "파일 경로를 직접 조회할 수 없습니다.")

    # SQL 여러 개 실행 방지
    without_last_semicolon = query_check.strip().rstrip(";")
    if ";" in without_last_semicolon:
        return (False, "한 번에 하나의 SQL만 실행할 수 있습니다.")

    return (True, "SQL 검증을 통과했습니다.")


# =========================================================
# SQL Executor
# =========================================================

def _uses_source(sql: str, key: str) -> bool:
    """SQL이 해당 출처 schema(예: musinsa.)를 쓰는지 확인합니다."""
    cleaned = _strip_comments_and_strings(sql, preserve_identifiers=True)
    pattern = rf'(?<![\w])(?:"{re.escape(key)}"|{re.escape(key)})\s*\.'
    return bool(re.search(pattern, cleaned, re.IGNORECASE))


# 이 문구로 시작하는 오류는 SQL 문제가 아니므로 Data Agent 재시도 대상이 아닙니다.
SOURCE_UNAVAILABLE_PREFIX = "데이터 출처를 일시적으로 사용할 수 없습니다"


def execute_sql_to_dataframe(sql: str):
    """
    DuckDB에서 SQL을 실행하고 pandas DataFrame을 반환합니다.

    선택 출처가 잠겨(수집 중) 이번 연결에서 빠졌는데 SQL이 그 출처를 쓰면,
    SQL 오류 대신 "잠시 후 다시 시도" 안내 오류를 반환합니다.
    """

    is_valid, message = validate_select_sql(sql)
    if not is_valid:
        return (None, message)

    # H&M 단일 출처 SQL은 명시적으로 설정한 경우 백엔드 API 우선 조회
    # 다른 출처 또는 출처 간 JOIN은 기존 로컬 DuckDB 경로 유지
    from agent_system.common_tools.execution_tools import hm_api

    if hm_api.enabled() and _uses_source(sql, "hm") and not any(
        _uses_source(sql, key) for key in source_keys(available_only=False) if key != "hm"
    ):
        frame, api_error, use_local = hm_api.query(sql)
        if api_error is None:
            return frame, None
        if not use_local:
            return None, f"H&M 백엔드 조회 오류: {api_error}"
        print(f"[H&M API] {api_error} → 로컬 DuckDB 대체 조회")

    try:
        con, skipped = open_duckdb_connection()
    except Exception as e:
        return (None, f"DuckDB 연결에 실패했습니다: {repr(e)}")

    try:
        unavailable = [
            item for item in skipped
            if item["reason"] == "locked_or_error" and _uses_source(sql, item["key"])
        ]
        if unavailable:
            labels = ", ".join(item["label"] for item in unavailable)
            return (
                None,
                f"{SOURCE_UNAVAILABLE_PREFIX}: {labels} "
                "(데이터 갱신 중일 수 있습니다. 잠시 후 다시 시도해 주세요.)",
            )

        try:
            df = con.execute(sql).fetchdf()
            error = None

        except Exception as e:
            df = None
            error = f"SQL 실행 오류: {repr(e)}"

    finally:
        con.close()

    return (df, error)


# =========================================================
# 0행 / 실행 오류 시 재시도 힌트
# =========================================================

# WHERE 조건에 자주 쓰이는 범주형 컬럼 (출처별 설정: common/data_sources.py의 category_hint_columns)
def category_hint_columns() -> dict:
    allowed = set(allowed_schemas())
    return {
        table: columns
        for table, columns in merged("category_hint_columns").items()
        if table.split(".", 1)[0] in allowed
    }


MAX_HINT_VALUES = 40


def build_filter_value_hint(sql: str) -> str:
    """SQL의 WHERE 조건에 쓰인 범주형 컬럼의 실제 값 목록을 문자열로 반환합니다.

    0행 결과가 나왔을 때 Data Agent가 실제 값으로 SQL을 고칠 수 있도록 돕습니다.
    조회 전용이며, 값이 많은 컬럼은 구매가 많은 순이 아니라 상품 수가 많은 순 상위만 보여 줍니다.
    """

    if not sql:
        return ""

    checked = _strip_comments_and_strings(sql)
    used = []
    for table_name, columns in category_hint_columns().items():
        for column in columns:
            if re.search(
                rf"\b{column}\b\s*(=|<>|!=|IN\b|LIKE\b|ILIKE\b|NOT\b)",
                checked,
                flags=re.IGNORECASE,
            ):
                used.append((table_name, column))

    lines = []
    try:
        con = get_analysis_connection()
    except Exception as e:
        print(f"[힌트 조회 연결 오류] {repr(e)}")
        return ""

    try:
        for table_name, column in used:
            rows = con.execute(
                f"""
                SELECT {column}, COUNT(*) AS n
                FROM {table_name}
                WHERE {column} IS NOT NULL
                GROUP BY 1
                ORDER BY n DESC
                LIMIT {MAX_HINT_VALUES}
                """
            ).fetchall()
            total = con.execute(
                f"SELECT COUNT(DISTINCT {column}) FROM {table_name}"
            ).fetchone()[0]
            values = ", ".join(f"'{row[0]}'" for row in rows)
            suffix = "" if total <= MAX_HINT_VALUES else f" … (전체 {total}종 중 상위 {MAX_HINT_VALUES})"
            lines.append(f"- {table_name}.{column}: {values}{suffix}")
    except Exception as e:
        print(f"[힌트 조회 오류] {repr(e)}")
    finally:
        con.close()

    # SQL에 쓰인 문자열 값이 다른 컬럼에 실제로 있는지 찾아 줍니다.
    # 예: product_group_name = 'Ladieswear' → 'Ladieswear'는 index_group_name 값
    literals = {
        value.replace("''", "'")
        for value in re.findall(r"'((?:[^']|'')*)'", sql)
        if value and "%" not in value and len(value) <= 60
    }
    if literals:
        try:
            con = get_analysis_connection()
            for literal in sorted(literals):
                found = []
                for table_name, columns in category_hint_columns().items():
                    for column in columns:
                        hit = con.execute(
                            f"SELECT {column} FROM {table_name} "
                            f"WHERE lower({column}) = lower(?) LIMIT 1",
                            [literal],
                        ).fetchone()
                        if hit:
                            found.append(f"{table_name}.{column} = '{hit[0]}'")
                if found:
                    lines.append(f"- 값 '{literal}'이 실제로 있는 컬럼: " + ", ".join(found))
            con.close()
        except Exception as e:
            print(f"[값 위치 조회 오류] {repr(e)}")

    return "\n".join(lines)
