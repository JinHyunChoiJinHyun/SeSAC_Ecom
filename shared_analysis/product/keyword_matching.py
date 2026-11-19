"""
키워드와 부합하는 상품 탐색
shared_analysis/product/keyword_matching.py
트렌드 키워드(주로 한국어) ↔ H&M 상품 속성(영문 정식 값) 매칭. 순수 계산 (DB·API 의존 없음)

  1) map_keywords : 키워드 → [(속성, H&M 정식 값)]. 사전·정식 값으로 바꿀 수 없는 키워드는 unmapped
  2) score        : 후보 상품마다 키워드가 맞는지 '값 단위로 정확히' 비교해 점수 매김
     (후보는 저장소가 DuckDB에서 1차로 거른 상품. 이 모듈은 전체 상품을 훑지 않는다)

규칙
  - 문자열 부분 일치를 쓰지 않는다 ('bra'가 'Bracelet'에 걸리는 문제)
  - 키워드를 이루는 단어가 하나라도 해석되지 않으면 키워드 전체를 unmapped로 둔다
    ('꾸안꾸 니트'를 '니트'로만 매칭해 놓고 '꾸안꾸 니트와 일치'라고 말하지 않도록)
  - 사전의 한 단어는 속성 하나에만 대응한다 (값은 여러 개 가능)
    → 키워드는 '자기가 가진 모든 속성'에서 상품 값이 맞을 때 일치
  - 한 키워드 안에서 여러 단어가 같은 속성을 가리키면 값의 교집합만 인정한다
    (교집합이 비면 unmapped 처리)
    ('블랙 블루 니트': 상품의 대표 색상은 하나뿐이라 확인할 수 없음 → 검정 니트를 '블랙 블루 니트'라 하지 않음)
"""

import unicodedata # 유니코드 정규화 모듈
from collections import defaultdict

# KeywordHit.attribute와 같은 이름. hm_vocab.json 키는 복수형(이름 + "s")
# 순서 = 영문 정식 값이 여러 속성에 동시에 있을 때의 우선순위 (사전에 등록된 단어는 사전이 우선)
#   color_master를 color보다 앞에: 'blue'는 세부 색상 'Blue' 하나가 아니라 Blue 계열 전체(Dark Blue 등)로 읽는 게
#   사용자 의도에 가깝다. 'dark blue'처럼 세부 색상에만 있는 값은 그대로 color로 해석된다
# 실제 vocab에서 겹치는 값은 ambiguous_terms(vocab)로 확인하고, 필요하면 사전에 명시한다
# 파생 테이블 컬럼과 통일
ATTRIBUTES = ("product_type", "garment_group", "product_group", "color_master", "color", "tone", "appearance")

def normalize_text(value: str) -> str:
    """NFC 정규화 + 소문자 + 공백 정리"""
    return " ".join(unicodedata.normalize("NFC", str(value)).lower().split())

# 한글 키워드를 H&M 상품 데이터의 실제 속성명과 속성값으로 변환하기 위한 매핑 사전
# 현재는 Python 코드에 하드코딩하여 관리하며, 추후 다음 방식 중 하나로 확장 검토
# 1. DB 관리: PostgreSQL에 매핑 테이블을 생성하여 관리
# 2. LLM 활용: 미등록 키워드의 속성과 값을 추론하고 실제 상품 데이터와 대조하여 검증
DICTIONARY_ENTRIES: list[tuple[str, str, list[str]]] = [
    ("니트", "garment_group", ["Knitwear"]),
    ("가디건", "product_type", ["Cardigan"]),
    ("스웨터", "product_type", ["Sweater"]),
    ("퍼플", "color_master", ["Lilac Purple"]),
    ("보라", "color_master", ["Lilac Purple"]),
    ("브라운", "color_master", ["Brown"]),
    ("네이비", "color", ["Dark Blue"]),
    ("다크", "tone", ["Dark"]),
    ("스트라이프", "appearance", ["Stripe"]),
]

def _build_dictionary(entries: list[tuple[str, str, list[str]]]) -> dict[str, tuple[str, list[str]]]:
    errors: list[str] = []
    dictionary: dict[str, tuple[str, list[str]]] = {}

    for i, entry in enumerate(entries):
        # 각 항목이 (한글 키워드, 속성명, 속성값 목록)의 튜플 형태인지 확인
        if not (isinstance(entry, tuple) and len(entry) == 3):
            errors.append(f"{i}번쨰 항목 형식 오류: {entry!r}")
            continue
        
        keyword, attribute, attribute_values = entry # 튜플 분해

        # 키워드 타입 검증
        if not isinstance(keyword, str):
            errors.append(f"{i}번째 항목의 키워드가 문자열이 아님: {entry!r}")
            continue
        
        # 키워드 빈 값 검증
        if not keyword.strip():
            errors.append(f"{i}번째 항목의 키워드가 비어 있음: {entry!r}")
            continue

        # 속성명 검증
        if not isinstance(attribute, str) or attribute not in ATTRIBUTES:
            errors.append(f"'{keyword}' 속성명 오류: {attribute!r}")
            continue

        # 속성값 요소 타입 및 유효성 검증
        if (not isinstance(attribute_values, list) 
            or not attribute_values 
            or not all(isinstance(v, str) and v.strip() for v in attribute_values)
          ):
            errors.append(f"'{keyword}' 값 목록은 비어 있지 않은 문자열 목록이어야 함: {attribute_values!r}")
            continue

        key = normalize_text(keyword)

        # 정규화된 중복 키 사전 검증으로 기존 값 덮어쓰기 방지
        if key in dictionary:
            errors.append(f"'{keyword}' 중복 (정규화 시 '{key}') ")
            continue
        
        # 중복 속성값 제거 후 (속성명, 속성값 목록) 형태의 튜플로 저장
        dictionary[key] = (attribute, list(dict.fromkeys(attribute_values)))

        if errors:
            raise ValueError("키워드 사전 오류: \n " + "\n ".join(errors))
        
        return dictionary

# 입력된 키워드와 H&M 상품 속성을 매칭하기 위한 조회용 딕셔너리
KEYWORD_DICTIONARY = _build_dictionary(DICTIONARY_ENTRIES)
# ──────────────────────────────────────────────────────────────────────────────
# 키워드 해석
# ──────────────────────────────────────────────────────────────────────────────

def _build_normalized_vocab_lookup(vocab: dict[str, tuple[str, ...]]) -> dict[str, dict[str, str]]:
    """속성별로 정규화된 속성값을 Key로, 정규화 전 원본 속성값을 Value로 저장하는 dict 생성"""
    return {
        attr: {normalize_text(v): v for v in vocab.get(f"{attr}s", ()) if isinstance(v,str) and v.strip()}
        for attr in ATTRIBUTES
    }

def _match_keyword_to_attribute(term: str, lookup: dict[str, dict[str, str]]) -> tuple[str, list[str]] | None:
    """키워드를 상품 속성명과 실제 속성값에 매칭"""
    # 1. 사전에 등록된 키워드인지 확인
    if term in KEYWORD_DICTIONARY:
        # 1) 키워드에 매핑된 상품 속성명과 속성값 목록 조회
        attribute, attribute_values = KEYWORD_DICTIONARY[term]
        # 2) 정규화한 속성값이 해당 속성의 lookup에 존재하는지 확인
        matched_values = [lookup[attribute][normalize_text(v)] for v in attribute_values if normalize_text(v) in lookup[attribute]]
        return (attribute, matched_values) if matched_values else None

    # 2. 사전에 없는 키워드는 모든 상품 속성의 lookup에서 직접 탐색
    for attr in ATTRIBUTES:
        if term in lookup[attr]:
            return attr, [lookup[attr][term]]

    # 매칭 실패 시 None 반환
    return None

def _merge_parts(parts: list[tuple[str, list[str]]]) -> dict[str, list[str]] | None:
    """
    속성별로 값을 병합하고, 동일 속성의 교집합만 유지 
    교집합이 비어 있으면 동일 속성에 대한 조건이 충돌한 것으로 판단
    """
    merged: dict[str, list[str]] = {}
    for attribute, attribute_values in parts:
        if attribute in merged:
            # 두 속성값 목록의 교집합 계산
            common = [v for v in merged[attribute] if v in attribute_values]

            # 교집합이 없으면 충돌로 간주
            if not common:
                return None
            
            # 교집합으로 이전 값 덮어쓰기
            merged[attribute] = common
        else:
            # 새로운 속성과 속성값 추가 (중복 제거)
            merged[attribute] = list(dict.fromkeys(attribute_values))
    return merged
def map_keywords(
        keywords: list[str], vocab: dict[str, tuple[str, ...]]
) -> tuple[dict[str, list[tuple[str,str]]], list[str]]:
    


        