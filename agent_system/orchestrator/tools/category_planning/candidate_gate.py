"""워크플로 8단계: 실제 Tool 결과에 근거한 상품 후보 검증."""
from __future__ import annotations
import re

PRODUCT_TOOLS = ('compare_product_candidates', 'get_product_details', 'match_product_keywords', 'search_products')

def validated_candidates(result: dict) -> list[str]:
    """LLM 텍스트/요청 인자가 아니라 Tool의 실제 반환 행에서만 상품 ID 추출."""
    data = result.get('product_result') or {}
    ids: list[str] = []
    for key in PRODUCT_TOOLS:
        section = data.get(key) or {}
        if not isinstance(section, dict):
            continue
        for row in section.get('rows') or []:
            if not isinstance(row, dict):
                continue
            value = str(row.get('article_id') or '').strip()
            if re.fullmatch(r'\d{10}', value) and value not in ids:
                ids.append(value)
    return ids

def gate_result(result: dict) -> tuple[bool, list[str]]:
    ids = validated_candidates(result)
    return bool(ids) and not result.get('error') and not result.get('not_ready'), ids
