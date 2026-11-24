"""무신사 통합 랭킹의 카테고리-상품명 적합성 점검.

원본을 재분류하거나 삭제하지 않는다. 상품명만으로 판정한 참고 플래그.
"""
from __future__ import annotations
import re
import pandas as pd

# 다른 대분류를 상품 유형으로 명시한 경우에만 자동 불일치.
OTHER_CATEGORY = re.compile(r"긴소매\s*티셔츠|반소매\s*티셔츠|반팔\s*티셔츠|long\s*sleeve\s*tee|short\s*sleeve\s*tee", re.I)
# 단독으로는 아우터 여부를 확정할 수 없는 품목명.
AMBIGUOUS = re.compile(r"트랙탑|풀오버|스냅[- ]?티|후드\s*집업|후디\s*집업", re.I)
OUTER = re.compile(r"패딩|다운|푸퍼|코트|자켓|재킷|파카|점퍼|무스탕|시어링|플리스|후리스|아노락|블레이저|블루종|보머|봄버|jacket|coat|parka|puffer|fleece|jumper", re.I)

def classify_outer(name: str) -> tuple[str,str]:
    name = str(name or '')
    if OTHER_CATEGORY.search(name):
        return '불일치 의심', '다른 대분류(티셔츠) 상품명 명시; 수동 확인 필요'
    # 아우터 명칭이 명확한 경우 트랙탑/후드 등의 동시 표기는 아우터일 수 있음.
    if OUTER.search(name):
        return '아우터 근거 확인', '아우터 유형 표현 확인(상품명 기반)'
    if AMBIGUOUS.search(name):
        return '판정 보류', '경계 상품: 상세 카테고리 확인 필요'
    return '판정 보류', '상품명만으로 아우터 여부 판단 불가'

def audit_outer(products: pd.DataFrame) -> pd.DataFrame:
    required={'item_id','name'}
    if not required.issubset(products.columns):
        raise ValueError(f'무신사 적합성 점검 필수 컬럼 누락: {required-set(products.columns)}')
    out=products.copy()
    result=out['name'].map(classify_outer)
    out['audit_status']=result.map(lambda a:a[0])
    out['audit_reason']=result.map(lambda a:a[1])
    return out

def subtype_evidence_pool(products: pd.DataFrame) -> pd.DataFrame:
    """명백 불일치·판정 보류는 소분류 자동 근거에서 제외; 전체 100개 원본은 유지."""
    audited=audit_outer(products)
    return audited.loc[audited['audit_status'].eq('아우터 근거 확인')].copy()
