from typing import Any
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from .sql_utils import (
    build_in_clause,
    escape_like,
    fetch_dicts
)

# API 스키마 필드 → hm.articles 컬럼 매핑
FIELD_COLUMNS: dict[str, str] = {
    "article_id": "a.article_id",
    "product_code": "a.product_code",
    "product_name": "a.prod_name",
    "product_type": "a.product_type_name",
    "product_group": "a.product_group_name",
    "index_group": "a.index_group_name",
    "section": "a.section_name",
    "department": "a.department_name",
    "garment_group": "a.garment_group_name",
    "color": "a.colour_group_name",
    "color_master": "a.perceived_colour_master_name",
    "tone": "a.perceived_colour_value_name",
    "appearance": "a.graphical_appearance_name",
}

# ProductFilter 필드 → API 스키마 필드 컬럼 매핑
FILTER_FIELDS: dict[str, str] = {
    "index_groups": "index_group",
    "product_groups": "product_group",
    "product_types": "product_type",
    "garment_groups": "garment_group",
    "colors": "color",
    "color_masters": "color_master",
    "tones": "tone",
    "appearances": "appearance",
}

_SUMMARY_SELECT = ",\n   ".join(f"{col} AS {field}" for field, col in FIELD_COLUMNS.items())

_FROM = """FROM hm.articles AS a"""

class HMProductRepository:
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def _build_filter_conditions(filters: dict[str, list[str]] | None = None) -> tuple[list[str], list[Any]]:
        """검색 필터를 SQL where 조건과 바인딩 파라미터로 변환"""
        conditions, params = [], [] # where 조건 목록, 바인딩 파라미터 목록
        for filter_name, values in (filters or {}).items():
            if not values:
                continue
            column = FIELD_COLUMNS[FILTER_FIELDS[filter_name]]
            clause, param = build_in_clause(column, values)
            conditions.append(clause)
            params.extend(param)
        return conditions, params

    # ── search_products ────────────────────────────────────────────────────
    def search(
            self,
            keyword: str | None,
            filters: dict[str, list[str]] | None = None,
            limit: int = 20,
            offset: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        """상품 검색을 위한 필터 및 키워드 조건 구성과 조회"""
        conditions, params = self._build_filter_conditions(filters)
        if keyword:
            pattern = f"%{escape_like(keyword)}%"
            conditions.append(
                "(a.prod_name ILIKE ? ESCAPE '\\' OR a.detail_desc ILIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern]) # ?(바인딩 파라미터)가 두개이므로 각 컬럼의 검색 조건에 전달

        where = "WHERE " + " AND ".join(conditions) if conditions else "" # condition의 0번쨰 요소에는 AND가 붙지 않음

        total_count = self.connection.execute(f"SELECT COUNT(*) FROM hm.articles AS a {where}", params).fetchone()[0]
        rows = fetch_dicts(
            self.connection,
            f"""
            SELECT
                {_SUMMARY_SELECT}
            {_FROM}
            {where}
            ORDER BY a.article_id
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        )

        return total_count, rows
        
    # ── get_product_details ────────────────────────────────────────────────
    def get_detail(self, article_id: str) -> dict[str, Any] | None:
        """상품 ID로 상품 요약 정보와 상세 설명 조회"""
        rows = fetch_dicts(
            self.connection,
            f"""
            SELECT
                {_SUMMARY_SELECT},
                a.detail_desc AS detail_desc
            {_FROM}
            WHERE a.article_id = ?
            """,
            [article_id]
        )
        return rows[0] if rows else None

    def get_many(self, article_ids: list[str]) -> list[dict[str, Any]]:
        """여러 상품 ID로 상품 요약 정보 조회"""
        if not article_ids:
            return []
        clause, params = build_in_clause("a.article_id", article_ids)
        return fetch_dicts(
            f"SELECT {_SUMMARY_SELECT} {_FROM} WHERE {clause}",
            params
        )

    def list_product_variants(self, product_code: str, exclude: str) -> list[str]:
        """동일 제품이지만 속성이 다른 상품 ID 조회"""
        rows = self.connection.execute(
            """
            SELECT article_id
            FROM hm.articles
            WHERE product_code = ? AND article_id <> ?
            ORDER BY article_id
            """,
            [product_code, exclude]
        ).fetchall()

        return [row[0] for row in rows]

    def find_by_attributes(
        self,
        attributes: dict[str, list[str]],
        filters: dict[str, list[str]],
        limit: int
    ) -> list[dict[str,Any]]:
        """상품 속성 및 추가 필터 조건으로 상품 검색"""
        if not attributes:
            return []
        any_clauses, params = [], []

        for field, values in attributes.items():
            clause, param = build_in_clause(FIELD_COLUMNS[field], values)
            any_clauses.append(clause)
            params.extend(param)

        conditions, filter_params = self._build_filter_conditions(filters)
        conditions.insert(0, "(" + " OR ".join(any_clauses) + ")")
        params.extend(filter_params)

        return fetch_dicts(
            self.connection,
            f"""
            SELECT {_SUMMARY_SELECT}
            {_FROM}
            WHERE {" AND ".join(conditions)}
            ORDER BY a.article_id
            LIMIT ?
            """,
            params + [limit]
        )
    