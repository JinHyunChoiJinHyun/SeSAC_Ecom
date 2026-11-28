"""트렌드 키워드 ↔ H&M 상품 속성 매칭 (아키텍처 shared_analysis/product/keyword_matching.py).

순수 Python: DB·API·LLM에 의존하지 않습니다. 입력은 상품 DataFrame, 출력은 dict입니다.
backend(product_service)와 agent_system 로컬 모드가 같은 함수를 씁니다.

구성
    CATEGORY_RULES   한국어 카테고리 → H&M 컬럼 조건 (니트 → garment_group_name = Knitwear …)
    COLOR_RULES      한국어 색상 → H&M 색상 컬럼 값 (네이비 → colour_group_name = Dark Blue …)
    GENDER_RULES     성별·대상 → index_group_name
    STYLE_TAG_RULES  스타일 태그 사전: prod_name·detail_desc(영문)에서 찾는 표현 (규칙 기반)
    extract_style_tags()  상품 1개의 스타일 태그 (아키텍처 product_features '스타일 태그'의 규칙판)
    parse_keyword()       "하프집업 니트" → 카테고리·색상·태그로 분해
    match_keywords()      키워드별 일치 상품 수·일치율·상위 상품

규칙 기반이라 사전에 없는 표현은 찾지 못합니다. 결과의 unmatched_terms로 확인하고 사전에 추가하십시오.
판정 기준(정규식)은 실제 H&M articles의 문장 표현을 보고 정했습니다 (예: 하프집업 = "zip at the top").
"""

from __future__ import annotations

import re

import pandas as pd

# ---------------------------------------------------------------------------
# 사전 (값을 바꾸면 검색·매칭 결과가 바로 바뀝니다)
# ---------------------------------------------------------------------------
# 카테고리: {한국어: [(컬럼, [값…]), …]}  — 조건 여러 개는 OR
CATEGORY_RULES: dict[str, list[tuple[str, list[str]]]] = {
    "니트": [("garment_group_name", ["Knitwear"])],
    "스웨터": [("product_type_name", ["Sweater"])],
    "가디건": [("product_type_name", ["Cardigan"])],
    "카디건": [("product_type_name", ["Cardigan"])],
    "후드티": [("product_type_name", ["Hoodie"])],
    "티셔츠": [("product_type_name", ["T-shirt"])],
    "셔츠": [("product_type_name", ["Shirt"])],
    "블라우스": [("product_type_name", ["Blouse"])],
    "원피스": [("product_type_name", ["Dress"])],
    "드레스": [("product_type_name", ["Dress"])],
    "바지": [("product_type_name", ["Trousers"])],
    "팬츠": [("product_type_name", ["Trousers"])],
    "청바지": [("garment_group_name", ["Trousers Denim"])],
    "데님": [("garment_group_name", ["Trousers Denim"])],
    "스커트": [("product_type_name", ["Skirt"])],
    "치마": [("product_type_name", ["Skirt"])],
    "반바지": [("product_type_name", ["Shorts"])],
    "재킷": [("product_type_name", ["Jacket", "Blazer"])],
    "자켓": [("product_type_name", ["Jacket", "Blazer"])],
    "코트": [("product_type_name", ["Coat"])],
    "아우터": [("garment_group_name", ["Outdoor"])],
}

# 색상: {한국어: (컬럼, [값…], 안내)}  — H&M에 같은 이름의 값이 없으면 가장 가까운 값과 안내를 둡니다.
COLOR_RULES: dict[str, tuple[str, list[str], str]] = {
    "블랙": ("colour_group_name", ["Black"], ""),
    "검정": ("colour_group_name", ["Black"], ""),
    "화이트": ("colour_group_name", ["White", "Off White"], ""),
    "흰색": ("colour_group_name", ["White", "Off White"], ""),
    "아이보리": ("colour_group_name", ["Off White", "Light Beige"], "H&M에 Ivory 값이 없어 Off White·Light Beige로 봅니다"),
    "크림": ("colour_group_name", ["Off White", "Light Beige"], "H&M에 Cream 값이 없어 Off White·Light Beige로 봅니다"),
    "그레이": ("perceived_colour_master_name", ["Grey"], ""),
    "회색": ("perceived_colour_master_name", ["Grey"], ""),
    "네이비": ("colour_group_name", ["Dark Blue"], "H&M에 Navy 값이 없어 Dark Blue로 봅니다"),
    "블루": ("perceived_colour_master_name", ["Blue"], ""),
    "파랑": ("perceived_colour_master_name", ["Blue"], ""),
    "브라운": ("perceived_colour_master_name", ["Brown"], ""),
    "갈색": ("perceived_colour_master_name", ["Brown"], ""),
    "베이지": ("perceived_colour_master_name", ["Beige"], ""),
    "카키": ("perceived_colour_master_name", ["Khaki green"], ""),
    "그린": ("perceived_colour_master_name", ["Green"], ""),
    "초록": ("perceived_colour_master_name", ["Green"], ""),
    "레드": ("perceived_colour_master_name", ["Red"], ""),
    "빨강": ("perceived_colour_master_name", ["Red"], ""),
    "버건디": ("colour_group_name", ["Dark Red"], "H&M에 Burgundy 값이 없어 Dark Red로 봅니다"),
    "핑크": ("perceived_colour_master_name", ["Pink"], ""),
    "퍼플": ("perceived_colour_master_name", ["Lilac Purple"], "H&M 대표 색 계열 Lilac Purple 기준"),
    "보라": ("perceived_colour_master_name", ["Lilac Purple"], "H&M 대표 색 계열 Lilac Purple 기준"),
    "라벤더": ("colour_group_name", ["Light Purple"], "H&M에 Lavender 값이 없어 Light Purple로 봅니다"),
    "옐로": ("perceived_colour_master_name", ["Yellow"], ""),
    "노랑": ("perceived_colour_master_name", ["Yellow"], ""),
    "오렌지": ("perceived_colour_master_name", ["Orange"], ""),
    "민트": ("perceived_colour_master_name", ["Turquoise"], "H&M에 Mint 값이 없어 Turquoise 계열로 봅니다"),
}

GENDER_RULES: dict[str, list[str]] = {
    "여성": ["Ladieswear"], "여자": ["Ladieswear"], "우먼": ["Ladieswear"],
    "남성": ["Menswear"], "남자": ["Menswear"], "맨": ["Menswear"],
    "아동": ["Baby/Children"], "키즈": ["Baby/Children"], "베이비": ["Baby/Children"],
    "스포츠": ["Sport"], "디바이디드": ["Divided"], "divided": ["Divided"],
}

# 스타일 태그: {태그: (분류, 정규식)}  — prod_name + detail_desc(소문자)에서 찾습니다.
STYLE_TAG_RULES: dict[str, tuple[str, str]] = {
    # 디자인 (넥라인·여밈)
    "하프집업": ("디자인", r"half[\s-]?zip|1/2 zip|zip at the top"),
    "풀집업": ("디자인", r"zip down the front|zip-through|full[\s-]?zip"),
    "후드": ("디자인", r"\bhood"),
    "터틀넥": ("디자인", r"turtle[\s-]?neck|polo[\s-]?neck"),
    "스탠드칼라": ("디자인", r"stand-up collar|high collar"),
    "브이넥": ("디자인", r"v-neck"),
    "라운드넥": ("디자인", r"round[\s-]?neck|crew[\s-]?neck"),
    "숄칼라": ("디자인", r"shawl collar"),
    "단추여밈": ("디자인", r"buttons? down the front"),
    # 짜임·텍스처
    "케이블": ("짜임", r"cable[\s-]?knit|\bcable"),
    "골지": ("짜임", r"\brib[\s-]?knit|ribbed knit"),
    "파인니트": ("짜임", r"fine[\s-]?knit"),
    "청키니트": ("짜임", r"chunky"),
    "와플": ("짜임", r"waffle"),
    "포인텔": ("짜임", r"pointelle"),
    # 핏
    "오버사이즈": ("핏", r"oversized|loose[\s-]?fit|relaxed fit"),
    "크롭": ("핏", r"\bcropped|short(er)? model"),
    "드롭숄더": ("핏", r"dropped shoulders?"),
    "래글런": ("핏", r"raglan"),
    "슬림핏": ("핏", r"slim fit|fitted"),
    # 소재
    "울": ("소재", r"\bwool"),
    "캐시미어": ("소재", r"cashmere"),
    "모헤어": ("소재", r"mohair"),
    "알파카": ("소재", r"alpaca"),
    "코튼": ("소재", r"cotton"),
    "린넨": ("소재", r"linen|linnen"),
    "비스코스": ("소재", r"viscose|modal|lyocell"),
    "부클레": ("소재", r"bouclé|boucle"),
    # 무늬
    "스트라이프": ("무늬", r"stripe"),
    "자카드": ("무늬", r"jacquard"),
    "노르딕": ("무늬", r"nordic|fair isle"),
    # 형태
    "조끼": ("형태", r"sleeveless (jumper|sweater|top in a)|sweater vest|knitted vest|slipover"),
}

# 같은 뜻의 다른 표현 → 사전 태그 이름
TAG_SYNONYMS: dict[str, str] = {
    "반집업": "하프집업", "하프지퍼": "하프집업", "하프짚업": "하프집업", "집업": "풀집업",
    "꽈배기": "케이블", "립": "골지", "폴라": "터틀넥", "터틀": "터틀넥", "목폴라": "터틀넥",
    "오버핏": "오버사이즈", "루즈핏": "오버사이즈", "박시": "오버사이즈",
    "니트베스트": "조끼", "베스트": "조끼", "줄무늬": "스트라이프", "면": "코튼",
}

# 키워드에서 무시할 일반 단어
STOP_WORDS = {"상품", "제품", "옷", "의류", "신상", "요즘", "트렌드", "스타일", "룩"}

_COMPILED = {tag: (group, re.compile(pattern)) for tag, (group, pattern) in STYLE_TAG_RULES.items()}


def _missing(value) -> bool:
    """None·NaN·pd.NA이면 True (pd.NA를 or로 평가하면 예외)."""
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(*values) -> str:
    return " ".join(str(v) for v in values if not _missing(v)).lower()


def extract_style_tags(prod_name, detail_desc, graphical_appearance=None) -> list[str]:
    """상품명·상품 설명에서 스타일 태그 목록 (사전 순서)."""
    text = _text(prod_name, detail_desc)
    tags = [tag for tag, (_, pattern) in _COMPILED.items() if pattern.search(text)]
    appearance = "" if _missing(graphical_appearance) else str(graphical_appearance).lower()   # 결측이면 무늬 검사만 건너뜀
    if "stripe" in appearance and "스트라이프" not in tags:
        tags.append("스트라이프")
    if "jacquard" in appearance and "자카드" not in tags:
        tags.append("자카드")
    return tags


def tag_group(tag: str) -> str:
    return STYLE_TAG_RULES.get(tag, ("", ""))[0]


def add_style_tags(products: pd.DataFrame) -> pd.DataFrame:
    """products에 style_tags(list) 열을 붙여 돌려줍니다 (원본은 바꾸지 않음)."""
    out = products.copy()
    appearance = out["graphical_appearance_name"] if "graphical_appearance_name" in out else [None] * len(out)
    out["style_tags"] = [extract_style_tags(n, d, g) for n, d, g in
                         zip(out.get("prod_name", ""), out.get("detail_desc", ""), appearance)]
    return out


# ---------------------------------------------------------------------------
# 키워드 해석
# ---------------------------------------------------------------------------
def _dictionary_terms():
    """(용어, 종류) — 긴 말부터 찾도록 정렬 ("후드티"가 "후드"보다 먼저)."""
    terms = [(t, "category", t) for t in CATEGORY_RULES] + [(t, "color", t) for t in COLOR_RULES] + \
            [(t, "gender", t) for t in GENDER_RULES] + [(t, "tag", t) for t in STYLE_TAG_RULES] + \
            [(t, "tag", canonical) for t, canonical in TAG_SYNONYMS.items()]
    return sorted(terms, key=lambda x: -len(x[0]))


def parse_keyword(keyword: str) -> dict:
    """'하프집업 니트' → {category:['니트'], colors:[], genders:[], tags:['하프집업'], unmatched_terms:[]}

    띄어쓰기가 없어도("하프집업니트") 사전 단어를 앞에서부터 찾아 나눕니다.
    같은 뜻의 표현(TAG_SYNONYMS: 반집업·오버핏 …)은 사전 태그 이름으로 바꿉니다.
    """
    parsed = {"keyword": str(keyword).strip(), "category": [], "colors": [], "genders": [], "tags": [],
              "unmatched_terms": []}
    key_of = {"category": "category", "color": "colors", "gender": "genders", "tag": "tags"}
    terms = _dictionary_terms()
    for token in str(keyword).lower().split():
        rest = token
        found_any = False
        while rest:
            hit = next(((t, kind, c) for t, kind, c in terms if rest.startswith(t.lower())), None)
            if hit is None:
                break
            term, kind, canonical = hit
            bucket = parsed[key_of[kind]]
            if canonical not in bucket:
                bucket.append(canonical)
            rest = rest[len(term):]
            found_any = True
        rest = rest.strip()
        if rest and rest not in STOP_WORDS and not (found_any and len(rest) <= 1):
            parsed["unmatched_terms"].append(rest if found_any else token)
    return parsed


def _mask_for_rules(products: pd.DataFrame, rules: list[tuple[str, list[str]]]) -> pd.Series:
    mask = pd.Series(False, index=products.index)
    for column, values in rules:
        if column in products:
            mask |= products[column].isin(values)
    return mask


def category_mask(products: pd.DataFrame, categories: list[str]) -> pd.Series:
    mask = pd.Series(False, index=products.index)
    for category in categories:
        mask |= _mask_for_rules(products, CATEGORY_RULES.get(category, []))
    return mask


def color_mask(products: pd.DataFrame, colors: list[str]) -> pd.Series:
    mask = pd.Series(False, index=products.index)
    for color in colors:
        column, values, _ = COLOR_RULES[color]
        if column in products:
            mask |= products[column].isin(values)
    return mask


def gender_mask(products: pd.DataFrame, genders: list[str]) -> pd.Series:
    values = [v for g in genders for v in GENDER_RULES.get(g, [])]
    # 성별 열이 없으면 성별을 확인할 수 없으므로 미충족 (CodeRabbit 리뷰: 근거 없이 완전 일치 금지)
    return products["index_group_name"].isin(values) if "index_group_name" in products else \
        pd.Series(False, index=products.index)


def tag_mask(products: pd.DataFrame, tags: list[str]) -> pd.Series:
    """모든 태그를 가진 상품 (AND)."""
    tag_sets = products["style_tags"].map(set)
    return tag_sets.map(lambda s: all(t in s for t in tags))


def mapping_notes(parsed: dict) -> list[str]:
    """한국어 → H&M 값 변환 안내 (답변의 근거·한계 표시용)."""
    notes = []
    for category in parsed["category"]:
        rule = " 또는 ".join(f"{c} ∈ {v}" for c, v in CATEGORY_RULES[category])
        notes.append(f"{category} → {rule}")
    for color in parsed["colors"]:
        column, values, note = COLOR_RULES[color]
        notes.append(f"{color} → {column} ∈ {values}" + (f" ({note})" if note else ""))
    for gender in parsed["genders"]:
        notes.append(f"{gender} → index_group_name ∈ {GENDER_RULES[gender]}")
    for tag in parsed["tags"]:
        notes.append(f"{tag} → 상품명·설명에 '{STYLE_TAG_RULES[tag][1]}' 표현 (규칙 기반 태그)")
    return notes


def match_keyword(products: pd.DataFrame, keyword: str) -> tuple[pd.DataFrame, dict]:
    """키워드 1개에 맞는 상품과 해석 결과.

    상품은 키워드의 모든 요소(카테고리·색상·성별·태그)를 만족해야 일치(AND)입니다.
    부분 일치 수준(match_rate)은 각 요소 중 몇 개를 만족했는지로 따로 계산합니다.
    products에는 style_tags 열이 있어야 합니다 (add_style_tags).
    """
    parsed = parse_keyword(keyword)
    checks = []
    if parsed["category"]:
        checks.append(("카테고리", category_mask(products, parsed["category"])))
    if parsed["colors"]:
        checks.append(("색상", color_mask(products, parsed["colors"])))
    if parsed["genders"]:
        checks.append(("성별", gender_mask(products, parsed["genders"])))
    for tag in parsed["tags"]:
        checks.append((tag, tag_mask(products, [tag])))
    if not checks:
        empty = products.iloc[0:0].copy()
        empty["match_rate"] = []
        return empty, parsed
    satisfied = sum(mask.astype(int) for _, mask in checks)
    scored = products.copy()
    scored["match_rate"] = satisfied / len(checks)
    scored["matched_parts"] = [
        [name for name, mask in checks if mask.iloc[i]] for i in range(len(scored))]
    parsed["parts"] = [name for name, _ in checks]
    return scored, parsed


def match_keywords(products: pd.DataFrame, keywords: list[str], top_n: int = 10) -> dict:
    """키워드별 완전 일치 상품 수·상품 모델 수, 부분 일치 수, 상위 상품.

    반환
        keywords: [{keyword, parsed, full_match_articles, full_match_products, partial_match_articles,
                    mapping_notes, unmatched_terms}]
        rows:     키워드별 상위 상품 (완전 일치 우선, 같은 일치율이면 article_id 순)
    """
    summaries, rows = [], []
    for keyword in keywords:
        scored, parsed = match_keyword(products, keyword)
        if scored.empty or "match_rate" not in scored or not parsed.get("parts"):
            summaries.append({"keyword": keyword, "parts": [], "full_match_articles": 0, "full_match_products": 0,
                              "partial_match_articles": 0, "mapping_notes": [],
                              "unmatched_terms": parsed["unmatched_terms"],
                              "note": "사전에 있는 카테고리·색상·태그 단어가 없어 매칭하지 못했습니다."})
            continue
        full = scored[scored["match_rate"] >= 1.0]
        partial = scored[(scored["match_rate"] > 0) & (scored["match_rate"] < 1.0)]
        summaries.append({
            "keyword": keyword,
            "parts": parsed["parts"],
            "full_match_articles": int(len(full)),
            "full_match_products": int(full["product_code"].nunique()) if "product_code" in full else int(len(full)),
            "partial_match_articles": int(len(partial)),
            "mapping_notes": mapping_notes(parsed),
            "unmatched_terms": parsed["unmatched_terms"],
        })
        top = scored[scored["match_rate"] > 0].sort_values(["match_rate", "article_id"], ascending=[False, True])
        for _, row in top.head(top_n).iterrows():
            rows.append({"keyword": keyword, "article_id": row["article_id"],
                         "product_code": row.get("product_code"), "prod_name": row.get("prod_name"),
                         "product_type_name": row.get("product_type_name"),
                         "colour_group_name": row.get("colour_group_name"),
                         "style_tags": list(row.get("style_tags") or []),
                         "match_rate": round(float(row["match_rate"]), 4),
                         "matched_parts": row["matched_parts"]})
    return {"keywords": summaries, "rows": rows,
            "rule": "완전 일치 = 키워드의 카테고리·색상·성별·태그를 모두 만족, match_rate = 만족한 요소 비율"}
