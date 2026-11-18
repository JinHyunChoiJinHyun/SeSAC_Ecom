"""무신사 유사 상품 리뷰 계산 — analyze_competitor_reviews.

입력
    products: DataFrame  item_id, name (+ ranking, brand 선택)        (무신사 상품·랭킹)
    reviews : DataFrame  item_id, satisfaction, review (+ review_id)    (무신사 리뷰)
유사 상품 = 상품명에 키워드가 들어간 상품 ('하프집업 니트'처럼 띄어 쓰면 두 단어가 모두 든 상품)
분류 규칙(아래 상수)은 샘플 형식 기준·일부 추정입니다. 실제 데이터를 보고 조정하십시오.
"""

from __future__ import annotations

import pandas as pd

from shared_analysis.market.common import clean_words, phrase_match, rnd, text_or_empty

TOP_PRODUCTS = 10           # 결과에 넣을 유사 상품 수

# satisfaction 예: "사이즈 정사이즈 · 두께감 적당함 · 색감 화면과 같아요"
SATISFACTION_SEPARATORS = ("·", "|", ",")
ASPECT_ALIASES = {"화면 대비 색감": "색감"}
ASPECTS = ("화면 대비 색감", "사이즈", "두께감", "신축성", "색감", "퀄리티", "구김", "재구매 의사")

# 항목별 응답 값 분류: OK 단어가 들어 있으면 '만족', NEG 단어면 '불만', 둘 다 아니면 '기타' (포함 검사)
ASPECT_VALUE_RULES = {
    "사이즈": {"ok": ("정사이즈", "잘 맞", "딱 맞"), "neg": ("작", "커", "크")},
    "두께감": {"ok": ("적당",), "neg": ("얇",)},
    "신축성": {"ok": ("적당", "있어", "좋"), "neg": ("없",)},
    "색감": {"ok": ("같아", "똑같", "비슷"), "neg": ("달라", "어두", "밝", "차이")},
    "퀄리티": {"ok": ("좋", "만족", "괜찮"), "neg": ("별로", "아쉬", "나빠", "낮")},
    "구김": {"ok": ("없", "적"), "neg": ("많", "있어", "잘 생")},
    "재구매 의사": {"ok": ("있",), "neg": ("없",)},
}

# 리뷰 본문에서 항목을 '말했는지' 찾는 표현 → 언급률 (가설, 실제 리뷰를 보고 조정)
# satisfaction(만족도 선택 항목)은 리뷰마다 모든 항목을 고르는 형식이라 언급률로 쓸 수 없습니다.
ASPECT_TEXT_TERMS = {
    "사이즈": ("사이즈", "크기", "정사이즈", "작아", "작게", "커요", "크게", "핏이", "기장"),
    "두께감": ("두께", "두껍", "두꺼", "얇"),
    "신축성": ("신축", "늘어나", "쫀쫀", "탄력"),
    "색감": ("색감", "색상", "컬러", "색이", "색깔"),
    "퀄리티": ("퀄리티", "품질", "마감", "박음질", "재질"),
    "구김": ("구김", "주름"),
    "재구매 의사": ("재구매", "또 살", "또 구매"),
}

# 리뷰 본문에서 찾는 불만 표현 (가설)
COMPLAINT_TERMS = {
    "보풀": ("보풀",),
    "따가움·까슬함": ("따가", "까슬", "까끌", "간지럽"),
    "늘어남": ("늘어나", "늘어짐", "늘어져"),
    "비침": ("비침", "비쳐", "비치"),
    "털빠짐": ("털빠짐", "털 빠", "털이 빠"),
    "얇음": ("얇아", "얇음", "얇네"),
    "수축": ("줄어", "수축"),
    "냄새": ("냄새",),
}
COMPLAINT_NEGATIONS = ("보풀 없", "보풀이 없", "보풀 안", "보풀이 안", "보풀 적", "보풀이 적")  # 칭찬은 제외


def similar_products(products: pd.DataFrame, keywords, exclude=None) -> pd.DataFrame:
    """상품명에 키워드(중 하나)가 들어간 상품. matched_keyword 열 추가, 랭킹 순."""
    keywords, exclude = clean_words(keywords), clean_words(exclude)

    def first_match(name):
        if any(phrase_match(name, e) for e in exclude):
            return None
        return next((k for k in keywords if phrase_match(name, k)), None)

    matched = products["name"].fillna("").map(first_match)
    found = products[matched.notna()].copy()
    found["matched_keyword"] = matched[matched.notna()]
    return found.sort_values("ranking") if "ranking" in found else found


def parse_satisfaction(text: str) -> dict[str, str]:
    parts = [text_or_empty(text)]           # 결측(None·NaN·pd.NA)은 빈 응답 (리뷰는 본문 집계에 그대로 포함)
    for sep in SATISFACTION_SEPARATORS:
        parts = [p for part in parts for p in part.split(sep)]
    found = {}
    for part in parts:
        part = part.strip()
        for aspect in ASPECTS:  # 긴 이름이 먼저
            if part.startswith(aspect):
                value = part[len(aspect):].strip()
                if value:
                    found[ASPECT_ALIASES.get(aspect, aspect)] = value
                break
    return found


def classify_value(aspect: str, value: str) -> str:
    rules = ASPECT_VALUE_RULES.get(aspect, {})
    if any(word in value for word in rules.get("ok", ())):
        return "만족"
    if any(word in value for word in rules.get("neg", ())):
        return "불만"
    return "기타"


def analyze_reviews(products: pd.DataFrame, reviews: pd.DataFrame, keywords, exclude=None) -> dict:
    keywords = clean_words(keywords)
    found = similar_products(products, keywords, exclude)
    reviews = reviews[reviews["item_id"].astype(str).isin(set(found["item_id"].astype(str)))]
    total = len(reviews)
    base = {"keywords": keywords, "similar_products": len(found), "reviews": total,
            "top_similar_products": [
                {"ranking": int(r.ranking) if "ranking" in found and pd.notna(r.ranking) else None,
                 "brand": text_or_empty(getattr(r, "brand", "")), "name": r.name, "matched_keyword": r.matched_keyword}
                for r in found.head(TOP_PRODUCTS).itertuples()]}
    if total == 0:
        return {**base, "rows": [], "complaint_terms": [],
                "notes": ["유사 상품 또는 리뷰가 없습니다. 키워드를 넓혀 보십시오."]}

    parsed = reviews["satisfaction"].map(parse_satisfaction)
    text = reviews["review"].fillna("").astype(str).str.replace(r"\s+", " ", regex=True)
    rows = []
    for aspect in ASPECT_VALUE_RULES:  # 사이즈 → 두께감 → … 순서
        values = [p[aspect] for p in parsed if aspect in p]
        terms = ASPECT_TEXT_TERMS.get(aspect, ())
        mentioned = int(text.map(lambda t: any(term in t for term in terms)).sum()) if terms else 0
        if not values and not mentioned:
            continue
        row = {"aspect": aspect, "mention_reviews": mentioned, "mention_rate": rnd(mentioned / total),
               "answered_reviews": len(values)}
        if values:
            labels = [classify_value(aspect, v) for v in values]
            counts = pd.Series(values).value_counts()
            row.update(satisfied_rate=rnd(labels.count("만족") / len(values)),
                       complaint_rate=rnd(labels.count("불만") / len(values)),
                       other_rate=rnd(labels.count("기타") / len(values)),
                       top_values={str(k): int(v) for k, v in counts.head(5).items()})
        rows.append(row)
    term_rows = []
    for term, patterns in COMPLAINT_TERMS.items():
        hit = text.map(lambda t: any(p in t for p in patterns))
        if term == "보풀":
            hit &= ~text.map(lambda t: any(n in t for n in COMPLAINT_NEGATIONS))
        if hit.any():
            term_rows.append({"term": term, "reviews": int(hit.sum()), "rate": rnd(hit.mean())})
    term_rows.sort(key=lambda r: -r["reviews"])
    return {
        **base, "rows": rows, "complaint_terms": term_rows,
        "basis": ("mention_rate = 리뷰 본문에서 그 항목을 말한 리뷰 ÷ 유사 상품 전체 리뷰 (고객이 자주 말하는 항목), "
                  "complaint_rate = 만족도 선택 항목 응답 중 '불만' 값 비율"),
        "notes": ["만족도 선택 항목(satisfaction)은 리뷰마다 모든 항목을 고르는 형식이라 언급률이 아니라 불만 비율에만 씁니다.",
                  "만족/불만 분류는 ASPECT_VALUE_RULES, 본문 언급은 ASPECT_TEXT_TERMS의 단어 규칙입니다 "
                  "(샘플 기준, '기타'는 분류 안 된 값 — top_values로 실제 값을 확인).",
                  "무신사 랭킹 스냅샷 상품 기준이며 판매량이 아닙니다."],
    }
