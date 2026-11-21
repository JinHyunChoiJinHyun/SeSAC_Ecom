"""무신사 수집 5개 카테고리 ↔ H&M 상품 분류. 근거 없는 소매 길이는 미분류로 둡니다."""
from __future__ import annotations
import pandas as pd

CATEGORIES = ("긴소매 티셔츠", "니트·스웨터", "반소매 티셔츠", "셔츠·블라우스", "아우터")


def category_masks(products: pd.DataFrame) -> dict[str, pd.Series]:
    """다섯 카테고리에 해당하는 행 마스크. 고객·판매 필터와 독립적입니다."""
    kind = products['product_type_name'].fillna('').astype(str)
    group = products['garment_group_name'].fillna('').astype(str)
    description = (products['prod_name'].fillna('').astype(str) + ' ' +
                   products['detail_desc'].fillna('').astype(str)).str.lower()
    long_sleeve = description.str.contains(r'long[\s-]?sleeved?|\bl/s\b', regex=True)
    short_sleeve = description.str.contains(r'short[\s-]?sleeved?|\bs/s\b', regex=True)
    tshirt = kind.eq('T-shirt')
    return {
        '긴소매 티셔츠': tshirt & long_sleeve & ~short_sleeve,
        '니트·스웨터': (group.eq('Knitwear') | kind.eq('Sweater')) & kind.isin(['Sweater', 'Cardigan', 'Top', 'Polo shirt']),
        '반소매 티셔츠': tshirt & short_sleeve & ~long_sleeve,
        '셔츠·블라우스': kind.isin(['Shirt', 'Blouse']),
        '아우터': (group.eq('Outdoor') | kind.isin(['Jacket', 'Coat'])) & kind.isin(['Jacket', 'Coat', 'Outdoor Waistcoat']),
    }


def categorize(products: pd.DataFrame, female_merchandise: bool = True) -> pd.DataFrame:
    """상품별 단일 카테고리 생성. 미확인 상품은 범주에서 제외하며 행은 보존합니다."""
    required = {'article_id', 'product_type_name', 'garment_group_name', 'prod_name', 'detail_desc', 'index_group_name'}
    missing = sorted(required - set(products.columns))
    if missing:
        raise ValueError(f'H&M 상품 컬럼 누락: {missing}')
    out = products.copy()
    out['musinsa_category'] = pd.Series(pd.NA, index=out.index, dtype='object')
    eligible = (out['index_group_name'].isin(['Ladieswear', 'Divided']) if female_merchandise
                else pd.Series(True, index=out.index))
    for name, mask in category_masks(out).items():
        overlap = eligible & mask & out['musinsa_category'].notna()
        if overlap.any():
            raise ValueError(f'카테고리 중복 매칭: {name}, {int(overlap.sum())}개')
        out.loc[eligible & mask, 'musinsa_category'] = name
    return out
