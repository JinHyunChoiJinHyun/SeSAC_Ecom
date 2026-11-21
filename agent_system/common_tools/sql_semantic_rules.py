"""분석 요청과 생성 SQL의 의미 일치 검증.

특정 상품명이나 데이터 출처에 종속되지 않는 공통 규칙입니다.
"""

import re


GUIDANCE = """
[분석 SQL 작성 규칙]
1. 사용자 요청에서 필터 조건, 비교 단위, 집계 지표, Top N을 구분하십시오.
2. 상품별 Top N은 상품 식별자별로, 색상별 Top N은 색상별로 집계하십시오.
3. '상품 유형별'처럼 비교 단위가 명시되면 그 단위를 우선하십시오.
4. 필터에 사용한 고정 분류 컬럼만 GROUP BY 해서 Top N을 만들지 마십시오.
5. Top N의 LIMIT은 비교 단위별 집계 후 적용하십시오.
6. 데이터에 없는 분류값·상품·수치는 만들어내지 마십시오.
7. 카테고리 등 실제 값이 불확실하면 스키마와 데이터 값 확인 Tool을 사용하십시오.
8. 구매 건수와 구매 수량, 매출액은 서로 다릅니다. 실제 스키마에 있는 지표를 사용하십시오.
9. 후속 요청에서는 사용자가 변경한 조건만 수정하고 나머지는 유지하십시오.
10. 서로 다른 분석 주제는 이전 SQL의 필터를 무조건 물려받지 마십시오.
"""


def guidance(question: str) -> str:
    return f"{GUIDANCE}\n[이번 분석 요청]\n{question}"


def semantic_issue(question: str, sql: str, row_count: int) -> str:
    """확실한 Top N 집계 단위 오류만 탐지합니다.

    한 행이 나왔다고 무조건 오류로 판단하지 않습니다.
    """
    question = str(question or "")
    sql = str(sql or "")

    if row_count != 1:
        return ""

    if not re.search(r"(?:top\s*[2-9]\d*|top\s*1\d+|상위\s*[2-9]\d*)", question, re.I):
        return ""

    group_match = re.search(
        r"\bGROUP\s+BY\s+(.*?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        sql,
        re.I | re.S,
    )
    where_match = re.search(
        r"\bWHERE\s+(.*?)(?=\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        sql,
        re.I | re.S,
    )

    if not group_match or not where_match:
        return ""

    group_columns = [
        col.strip().lower()
        for col in group_match.group(1).split(",")
    ]

    if not group_columns:
        return ""

    where_sql = where_match.group(1)

    # GROUP BY 컬럼 전체가 WHERE의 고정값으로 제한되었는지 확인
    for column in group_columns:
        if not re.fullmatch(
            r'(?:[a-z_][a-z_0-9]*\.)?[a-z_][a-z_0-9]*',
            column,
        ):
            return ""

        pattern = (
            rf"(?<![\w.]){re.escape(column)}\s*=\s*"
            r"(?:'[^']*'|\"[^\"]*\"|\d+)"
        )

        # 별칭 없이 쓴 컬럼도 확인하되, 모호한 경우는 오류로 판정하지 않음
        if not re.search(pattern, where_sql, re.I):
            return ""

    return (
        "Top N 요청인데 GROUP BY의 모든 컬럼이 WHERE에서 "
        "단일 값으로 고정되어 결과가 1행입니다. "
        "사용자가 요청한 실제 비교 단위(상품·색상·유형 등)를 "
        "다시 확인하여 SQL을 수정하십시오."
    )
