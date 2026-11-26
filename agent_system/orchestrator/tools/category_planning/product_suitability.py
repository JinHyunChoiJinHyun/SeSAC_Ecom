"""상품명·설명 기반 세부 유형 적합성 검토 (실측 성능 판정 아님)."""
from __future__ import annotations

import re
import pandas as pd

from .subtype_selection import canonical, subtype_mask

_LIGHT = re.compile(r'\b(?:light[ -]?weight|lightly padded|thinly padded|light padded)\b', re.I)
_PADDED = re.compile(r'\b(?:padded|puffer|down[ -]filled|filled with down)\b', re.I)
_CONFLICT_NAME = re.compile(r'\b(?:parka|shacket|coat|shirt jacket)\b', re.I)
_HEAVY = re.compile(r'\b(?:heavyweight|heavily padded|thickly padded)\b', re.I)


def evaluate_suitability(row: dict, subtype: str | None) -> dict:
    """명확한 규칙 근거와 상품명 충돌 여부만 판정. 보온·무게 등 성능은 검증하지 않는다."""
    key = canonical(subtype) if subtype else ''
    name = str(row.get('prod_name') or '')
    description = str(row.get('detail_desc') or '')
    combined = f'{name} {description}'
    if not key:
        return {'suitability_status': 'category_only', 'suitability_reason': '세부 유형 미지정',
                'suitability_evidence': '카테고리 및 구매 기록'}
    candidate = pd.DataFrame([{'prod_name': name, 'detail_desc': description, 'product_type_name': row.get('product_type_name', '')}])
    if not bool(subtype_mask(candidate, key).iloc[0]):
        return {'suitability_status': 'review_required', 'suitability_reason': '선택 세부 유형의 명시적 매칭 근거 부족',
                'suitability_evidence': '상품명·설명 규칙 불일치'}
    if key == '경량패딩':
        if _HEAVY.search(combined):
            return {'suitability_status': 'review_required', 'suitability_reason': '두꺼운 패딩 관련 표현과 충돌',
                    'suitability_evidence': '상품명·설명 추가 확인 필요'}
        if _CONFLICT_NAME.search(name) and not (_LIGHT.search(name) and _PADDED.search(name)):
            return {'suitability_status': 'review_required',
                    'suitability_reason': '상품명에 파카·셔켓·코트 유형이 나타나 경량패딩 여부 추가 확인 필요',
                    'suitability_evidence': '설명 규칙과 상품명 유형 충돌'}
        if not (_LIGHT.search(combined) and _PADDED.search(combined)):
            return {'suitability_status': 'review_required', 'suitability_reason': '경량 및 패딩 근거 동시 확인 불가',
                    'suitability_evidence': '상품명·설명 추가 확인 필요'}
    return {'suitability_status': 'rule_matched',
            'suitability_reason': '상품명·설명 규칙 일치 (실측 성능 미검증)',
            'suitability_evidence': '상품명·설명 기반 규칙'}


def classify_candidates(rows: pd.DataFrame, subtype: str | None) -> pd.DataFrame:
    """원본 구매 행·정렬·ID를 보존하며 판정 정보만 추가한다."""
    out = rows.copy()
    evaluations = [evaluate_suitability(row, subtype) for row in out.to_dict(orient='records')]
    for col in ('suitability_status', 'suitability_reason', 'suitability_evidence'):
        out[col] = pd.Series([item[col] for item in evaluations], index=out.index, dtype='object')
    return out
