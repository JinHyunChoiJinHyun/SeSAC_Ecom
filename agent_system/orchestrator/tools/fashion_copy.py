"""V4.22A: 검증된 제품 정보만 조합하는 패션 캠페인 카피 폴백.

카피의 정서/상황 표현은 창작이지만, 소재·핏·성능·가격은 추정하지 않는다.
헤드라인 3개와 각 안별 서브 카피 2개, 키워드 5개를 재현 가능하게 만든다.
"""
from __future__ import annotations

import re

_TRANSLATIONS = {'Black': '블랙', 'Greenish Khaki': '그린 카키', 'Beige': '베이지',
                 'White': '화이트', 'Dark Blue': '다크 블루'}


def make_fashion_copy(*, subtype: str, colors: list[str], points: list[str],
                      mood: str = '일상', korean: bool = False) -> dict:
    """세트의 상품 사실 주장은 색상과 유형에만 한정한다.

    '소재/핏'은 근거 없이 임의 삽입하지 않으며, 선택된 소구점은 검증 데이터로만 전달한다.
    """
    subtype = str(subtype or '').strip()
    colors = [str(x).strip() for x in colors if str(x).strip()]
    if not subtype or not colors or not points:
        raise ValueError('카피 생성에 필요한 확정 유형·색상·소구점이 없습니다.')
    shown = [_TRANSLATIONS.get(x, x) for x in colors] if korean else colors
    palette = '·'.join(shown)
    if mood == '감성':
        heads = [f'겨울의 순간, 나다운 {subtype}', f'취향으로 완성하는 {subtype}', f'{palette}, 나만의 겨울 무드']
    elif mood == '간결':
        heads = [f'나다운 {subtype}', f'{palette} {subtype}', f'겨울의 선택, {subtype}']
    else:
        heads = [f'겨울, 나다운 {subtype}', f'취향을 입는 겨울', f'{palette}의 새로운 무드']
    options = [
        {'headline': heads[0], 'tone': '일상의 세련미', 'subcopies': [
            f'분주한 하루에도 나다운 선택을. {palette} 컬러로 만나는 {subtype} 스타일링.',
            f'겨울의 매일을 위한 워드로브. {subtype}의 컬러와 상품별 디테일을 비교해 보세요.']},
        {'headline': heads[1], 'tone': '겨울 무드', 'subcopies': [
            f'나를 위한 겨울의 순간. {subtype} 컬렉션에서 취향에 맞는 컬러를 찾아보세요.',
            f'일상부터 특별한 하루까지. {palette}의 서로 다른 분위기를 소개합니다.']},
        {'headline': heads[2], 'tone': '컬렉션 큐레이션', 'subcopies': [
            f'하나의 계절, 다채로운 스타일. {palette} {subtype} 상품을 한눈에 살펴보세요.',
            f'새로운 겨울 워드로브를 위한 선택. 확인된 컬러와 디자인 설명을 상품별로 만나보세요.']},
    ]
    keywords = ['윈터 워드로브', '컬러 스타일링', '아우터 큐레이션', '상품별 디테일', '데일리 무드']
    if re.search('니트|스웨터', subtype):
        keywords[2] = '니트 큐레이션'
    return {'options': options, 'keywords': keywords}
