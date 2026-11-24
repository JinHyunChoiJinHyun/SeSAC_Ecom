"""네이버 개별 요청 검색어의 대분류 귀속·근거 사용 정책.

검색어별 0~100 정규화 지수는 *같은 검색어의 시계열*에만 사용합니다.
카테고리 간 검색량/시장점유율 점수를 만들거나 키워드를 합산하지 않습니다.
"""
from __future__ import annotations

from collections.abc import MutableMapping

# 최신 팀원 데이터(data.zip, 2026-11-23): 날짜 외 검색어 18개.
# 보조 키워드는 계절성/상품 분류가 모호하므로 별도 근거로 표현합니다.
CATEGORY_KEYWORDS = {
    "긴소매 티셔츠": {"primary": ("긴팔티", "롱슬리브"), "secondary": ("목폴라",)},
    "니트·스웨터": {"primary": ("니트", "스웨터"), "secondary": ("가디건",)},
    "반소매 티셔츠": {"primary": ("반팔티",), "secondary": ()},
    "셔츠·블라우스": {"primary": ("셔츠", "블라우스"), "secondary": ()},
    "아우터": {"primary": ("경량패딩", "패딩", "롱패딩", "숏패딩", "코트", "플리스", "무스탕", "재킷"), "secondary": ("바람막이",)},
}

KEYWORD_TO_CATEGORY = {
    keyword: category
    for category, tiers in CATEGORY_KEYWORDS.items()
    for words in tiers.values()
    for keyword in words
}


def keyword_tier(keyword: str) -> str:
    """카테고리별 근거 용도: 핵심/보조/미분류."""
    for category, tiers in CATEGORY_KEYWORDS.items():
        for tier, words in tiers.items():
            if keyword in words:
                return tier
    return "unmapped"


def apply_keyword_policy(mapping: MutableMapping[str, str]) -> MutableMapping[str, str]:
    """기존 shared_analysis 매핑을 삭제하지 않고 신규 수집 키워드만 명시적으로 정정."""
    mapping.update(KEYWORD_TO_CATEGORY)
    # 더 이상 수집하지 않는 두 저빈도 표현은 현재 CSV에는 없으며, 기존 정책은 보존합니다.
    return mapping


def audit_keywords(keywords: list[str]) -> dict:
    """새 컬럼이 늘어도 미분류를 임의 귀속하지 않음."""
    keys = set(keywords)
    expected = set(KEYWORD_TO_CATEGORY)
    return {
        "recognized": sorted(keys & expected),
        "unmapped": sorted(keys - expected),
        "missing": sorted(expected - keys),
        "primary": sorted(k for k in keys if keyword_tier(k) == "primary"),
        "secondary": sorted(k for k in keys if keyword_tier(k) == "secondary"),
        "normalization": "per_keyword_independent",
        "comparable_between_keywords": False,
    }
