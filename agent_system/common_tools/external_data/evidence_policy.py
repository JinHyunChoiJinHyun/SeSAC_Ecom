"""기획전 단계별 데이터 사용 정책 (추천 근거/보조/기본 미사용 구분).

데이터가 적재되어 있다는 이유로 매 단계 조회하지 않는다.
유튜브 후보표는 콘텐츠 주제를 설명할 때만 사용하며 판매량/예측 점수로 쓰지 않는다.
"""
from __future__ import annotations

STAGE_SOURCES = {
    'category_comparison': {'required': ('hm',), 'support': ('naver', 'musinsa'), 'not_default': ('youtube',)},
    'subtype_selection': {'required': ('hm',), 'support': ('naver', 'musinsa', 'youtube'), 'not_default': ()},
    'product_attributes': {'required': ('hm',), 'support': ('musinsa',), 'not_default': ('youtube',)},
    'selling_points': {'required': ('hm',), 'support': ('musinsa', 'youtube'), 'not_default': ()},
    'customer_targeting': {'required': ('hm',), 'support': ('purchase_model',), 'not_default': ('naver', 'musinsa', 'youtube')},
    'report': {'required': ('previously_verified_evidence',), 'support': (), 'not_default': ('fresh_external_query',)},
}


def sources_for(stage: str, *, requested_support=()) -> tuple[str, ...]:
    """명시된 보조 데이터 중 해당 단계에 허용된 것만 반환. 기본은 필수 소스만."""
    if stage not in STAGE_SOURCES:
        raise ValueError(f'등록되지 않은 분석 단계: {stage}')
    spec = STAGE_SOURCES[stage]
    unknown = set(requested_support) - set(spec['support'])
    if unknown:
        raise ValueError(f'{stage}에서 지원하지 않는 보조 근거: {sorted(unknown)}')
    return tuple(dict.fromkeys((*spec['required'], *requested_support)))
