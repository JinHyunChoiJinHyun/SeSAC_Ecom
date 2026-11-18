from enum import Enum

def _in_clause(column: str, enum_class: type[Enum]) -> str:
    """Enum 허용 값 만족 여부를 검사하는 CHECK 쿼리 생성"""
    return f"{column} IN (" + ", ".join(f"'{e.value}'" for e in enum_class) + ")"