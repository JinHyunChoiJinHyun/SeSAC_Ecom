"""소구점 계산 — analyze_selling_points.

유사 상품(review_analysis.similar_products)의 상품명·리뷰 본문에서 소재·핏·관리·디자인 표현 비율을 셉니다.
표현 사전(SELLING_POINT_TERMS, 정규식)에 있는 표현만 셉니다. 실제 데이터를 보고 추가·수정하십시오.
주의: "울"은 "겨울"과 겹치므로 앞 글자가 한글이 아닐 때만 셉니다.
"""

from __future__ import annotations

import re

import pandas as pd

from shared_analysis.market.common import clean_words, rnd
from shared_analysis.market.review_analysis import similar_products

SELLING_POINT_TERMS = {
    "소재": {
        "울": r"(?:^|[^가-힣])울(?!트라)|울\s*혼방|wool",
        "메리노울": r"메리노",
        "램스울": r"램스\s*울|lambswool",
        "캐시미어": r"캐시미어|캐시|cashmere",
        "알파카": r"알파카|alpaca",
        "모헤어": r"모헤어|mohair",
        "앙고라": r"앙고라|angora",
        "코튼": r"코튼|cotton|면\s*100|면혼방",
    },
    "핏": {
        "오버핏": r"오버\s*핏|오버\s*사이즈|오버사이즈드",
        "루즈핏": r"루즈\s*핏|루즈",
        "세미오버": r"세미\s*오버",
        "레귤러핏": r"레귤러",
        "슬림핏": r"슬림",
        "크롭": r"크롭",
    },
    "관리": {
        "보풀 적음": r"보풀\s*(?:이)?\s*(?:없|적|안)",
        "물세탁 가능": r"물\s*세탁|손\s*세탁|세탁기",
        "구김 적음": r"구김\s*(?:이)?\s*(?:없|적)",
        "부드러움": r"부드러",
        "따뜻함": r"따뜻|따듯|보온",
    },
    "디자인·넥": {
        "하프집업": r"하프\s*집업|반\s*집업|half\s*zip",
        "라운드넥": r"라운드",
        "브이넥": r"브이넥|v\s*넥|v-neck",
        "터틀넥": r"터틀넥|폴라",
        "케이블": r"케이블|꽈배기",
        "카라": r"카라(?!멜)|칼라(?!멜)",
        "스트라이프": r"스트라이프|줄무늬",
        "가디건": r"가디건|카디건",
        "베스트": r"베스트|조끼",
    },
}


def analyze_selling_points(products: pd.DataFrame, reviews: pd.DataFrame, keywords, exclude=None,
                           include_reviews: bool = True) -> dict:
    keywords = clean_words(keywords)
    found = similar_products(products, keywords, exclude)
    item_ids = set(found["item_id"].astype(str))
    reviews = reviews[reviews["item_id"].astype(str).isin(item_ids)] if include_reviews else reviews.iloc[0:0]
    names = found["name"].fillna("").astype(str).str.lower()
    texts = reviews["review"].fillna("").astype(str).str.lower()
    rows = []
    for group, terms in SELLING_POINT_TERMS.items():
        for term, pattern in terms.items():
            regex = re.compile(pattern, re.IGNORECASE)
            name_hit = int(names.map(lambda t: bool(regex.search(t))).sum()) if len(names) else 0
            review_hit = int(texts.map(lambda t: bool(regex.search(t))).sum()) if len(texts) else 0
            if name_hit or review_hit:
                rows.append({
                    "group": group, "term": term,
                    "product_names": name_hit, "product_share": rnd(name_hit / len(found)) if len(found) else None,
                    "reviews": review_hit, "review_share": rnd(review_hit / len(reviews)) if len(reviews) else None,
                })
    order = {g: i for i, g in enumerate(SELLING_POINT_TERMS)}
    rows.sort(key=lambda r: (order[r["group"]], -(r["product_share"] or 0), -(r["review_share"] or 0)))
    return {
        "keywords": keywords, "similar_products": len(found), "reviews": len(reviews), "rows": rows,
        "basis": "product_share = 상품명에 표현이 있는 유사 상품 비율, review_share = 리뷰 본문에 표현이 있는 리뷰 비율",
        "notes": ["표현 사전은 SELLING_POINT_TERMS (정규식)입니다. 사전에 없는 표현은 세지 않습니다.",
                  "상품명 빈도는 경쟁 상품이 내세우는 점, 리뷰 빈도는 고객이 실제로 말하는 점입니다."],
    }
