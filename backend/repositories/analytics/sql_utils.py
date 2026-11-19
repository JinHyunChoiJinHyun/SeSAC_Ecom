from typing import Any
import duckdb

def escape_like(text: str) -> str:
    """LIKE 조건절에서 와일드카드 문자를 문자 그대로 검색하도록 이스케이프 처리"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def build_in_clause(column: str, values: list[str]) -> tuple[str, list[str]]:
    """column IN (?, ?, ...) 조건과 파라미터 반환"""
    if not values:
        raise ValueError(f"{column} IN 조건에 넘길 값이 비어 있습니다")
    return f"{column} IN ({', '.join('?' for _ in values)})", list(values)

def fetch_dicts(connection:duckdb, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """쿼리 결과를 {컬럼 별칭: 값} dict 목록으로 반환"""
    cursor = connection.execute(sql, params)
    columns = [c[0] for c in cursor.description] # 컬럼명만 추출
    return [dict(zip(columns, row)) for row in cursor.fetchall()] # 컬럼명과 행 데이터를 매핑하여 각 행을 dict로 변환

