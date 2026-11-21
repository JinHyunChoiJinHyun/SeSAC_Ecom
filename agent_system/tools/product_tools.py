"""② Product Analysis Agent Tool 5개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    search_products             H&M 상품 카테고리·속성·스타일 태그 기반 검색
    match_product_keywords      트렌드 키워드와 자사 상품 속성 매칭
    analyze_product_sales       동일 기간·조건의 상품별 구매 실적 비교
    get_product_details         상품 설명, 소재, 색상, 이미지 등 정보 조회
    compare_product_candidates  후보 상품의 키워드 일치도와 판매 근거 비교

동작 모드 (아키텍처 [3] Tool → API → Service, scope: product:read)
- 백엔드 모드: CART_SIGNAL_BACKEND_URL이 있으면 backend_client로 아래 API를 호출합니다 (팀원 백엔드 준비 후).
      GET  /products/search          POST /products/keyword-match     POST /products/sales
      GET  /products/{article_id}    POST /products/compare
- 로컬 모드(지금): H&M DuckDB를 읽기 전용으로 조회(agent_system/common_tools/local_data/hm_products.py)하고
  shared_analysis/product(백엔드와 같은 계산 함수)로 계산합니다.
모든 Tool은 ToolResult를 반환합니다: {"available", "status", "source", "data", "evidence"}

기준일: H&M 마지막 거래일(아키텍처 ANALYSIS_AS_OF). "최근 N주"는 그 날부터 거꾸로 계산하고 결과에 실제 기간을 넣습니다.
나중에 최근 상품·판매 데이터(무신사·네이버 쇼핑 배치)를 쓰게 되면 LOCAL 모듈(또는 백엔드 API)만 바꿉니다.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from agent_system import backend_client
from agent_system.common_tools.local_data import hm_images as IMAGES
from agent_system.common_tools.local_data import hm_products as LOCAL
from shared_analysis.market.common import clean_words
from shared_analysis.product import keyword_matching as KM
from shared_analysis.product import product_analysis as PA

SOURCE = "H&M (자사 상품·구매 기록)"
MAX_IDS = 30                 # 판매·상세 조회 한 번에 받는 상품 수
DEFAULT_LIMIT = 20
NO_KEYWORD_NOTE = "사전에 있는 카테고리·색상·성별·스타일 태그 단어가 없어 조건을 만들지 못했습니다."


# ---------------------------------------------------------------- 공통
def _unavailable(status: str, **data) -> dict:
    return {"available": False, "status": status, "source": SOURCE, "data": data, "evidence": [],
            "note": "데이터가 없어 계산하지 않았습니다. 숫자·상품을 추정해서 채우지 마십시오."}


def _ok(data: dict, evidence: list[dict]) -> dict:
    return {"available": True, "status": LOCAL.status()["status"], "source": SOURCE, "data": data,
            "evidence": evidence}


def _local_ready() -> dict | None:
    info = LOCAL.status()
    return None if info["available"] else _unavailable(info["status"])


def normalize_ids(values) -> list[str]:
    """'108775015' → '0108775015' (H&M article_id는 앞자리 0 포함 10자리)."""
    out = []
    for value in clean_words(values):
        value = value.strip()
        if value.isdigit() and len(value) < 10:
            value = value.zfill(10)
        if value not in out:
            out.append(value)
    return out


def _product_evidence(article_ids) -> list[dict]:
    return [{"source_type": "hm_product", "source_id": f"hm_product:{a}", "label": f"H&M 상품 {a}"}
            for a in article_ids]


def _brief(row) -> dict:
    return {"article_id": row["article_id"], "product_code": row["product_code"], "prod_name": row["prod_name"],
            "product_type_name": row["product_type_name"], "colour_group_name": row["colour_group_name"],
            "index_group_name": row["index_group_name"], "style_tags": list(row["style_tags"])}


def _with_images(rows: list[dict]) -> list[dict]:
    """상품 행에 이미지 상태(image_available·image_path·image_status) 추가 (시나리오 9단계: 후보를 이미지와 함께 정리)."""
    images = IMAGES.lookup([r["article_id"] for r in rows if r.get("article_id")])
    return [{**r, **images.get(str(r.get("article_id")), {})} for r in rows]


def _filter_by_query(products: pd.DataFrame, query: str | None) -> tuple[pd.DataFrame, dict]:
    """한국어 조건 문장('여성 브라운 하프집업 니트') → 모든 조건을 만족하는 상품."""
    parsed = KM.parse_keyword(query or "")
    mask = pd.Series(True, index=products.index)
    if parsed["category"]:
        mask &= KM.category_mask(products, parsed["category"])
    if parsed["colors"]:
        mask &= KM.color_mask(products, parsed["colors"])
    if parsed["genders"]:
        mask &= KM.gender_mask(products, parsed["genders"])
    if parsed["tags"]:
        mask &= KM.tag_mask(products, parsed["tags"])
    return products[mask], parsed


# ---------------------------------------------------------------- 1
def search_products(query: str, name_contains: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    """H&M 상품 카테고리·속성·스타일 태그 기반 검색.

    query: 한국어 조건 (예: "여성 브라운 하프집업 니트"). 카테고리·색상·성별·스타일 태그를 모두 만족하는 상품(AND).
    name_contains: 상품명·설명의 영문 단어로 추가 필터 (선택, 예: "mohair").
    limit: 돌려줄 상품 수 (최대 50). 전체 건수·태그 분포는 limit과 관계없이 전체 기준입니다.
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), 50))
    if backend_client.enabled():
        return backend_client.call("GET", "/products/search", source=SOURCE,
                                   params={"query": query, "name_contains": name_contains, "limit": limit})
    if (blocked := _local_ready()) is not None:
        return blocked
    products = LOCAL.articles()
    found, parsed = _filter_by_query(products, query)
    has_condition = any(parsed[k] for k in ("category", "colors", "genders", "tags"))
    if name_contains:
        word = str(name_contains).lower()
        text = (found["prod_name"].fillna("") + " " + found["detail_desc"].fillna("")).str.lower()
        found = found[text.str.contains(word, regex=False)]
        has_condition = True
    if not has_condition:
        return _ok({"query": query, "total_articles": 0, "total_products": 0, "rows": [],
                    "unmatched_terms": parsed["unmatched_terms"], "note": NO_KEYWORD_NOTE}, [])
    found = found.sort_values("article_id")
    tag_counts = Counter(tag for tags in found["style_tags"] for tag in tags)
    data = {
        "query": query,
        "conditions": KM.mapping_notes(parsed) + ([f"상품명·설명에 '{name_contains}' 포함"] if name_contains else []),
        "unmatched_terms": parsed["unmatched_terms"],
        "total_articles": int(len(found)),
        "total_products": int(found["product_code"].nunique()),
        "colour_counts": found["colour_group_name"].value_counts().head(10).to_dict(),
        "style_tag_counts": dict(tag_counts.most_common(15)),
        "rows": _with_images([_brief(row) for _, row in found.head(limit).iterrows()]),
        "basis": "H&M articles 전체 (article_id = 색상별 상품, product_code = 상품 모델)",
    }
    if parsed["unmatched_terms"]:
        data["note"] = f"사전에 없는 단어는 조건에서 빠졌습니다: {parsed['unmatched_terms']}"
    return _ok(data, _product_evidence(found["article_id"].head(limit)))


# ---------------------------------------------------------------- 2
def match_product_keywords(keywords: list[str], base_query: str | None = None, top_n: int = 5) -> dict:
    """트렌드 키워드와 자사 상품 속성 매칭.

    keywords: 트렌드 키워드 목록 (예: ["하프집업 니트", "퍼플 니트", "케이블 니트"]).
    base_query: 매칭 범위를 먼저 좁힐 조건 (선택, 예: "여성 니트").
    top_n: 키워드별로 돌려줄 상위 상품 수 (최대 20).
    키워드별 완전 일치 상품 수(=카테고리·색상·태그 모두 만족)와 일치율, 한국어→H&M 값 변환 근거를 돌려줍니다.
    """
    keywords = clean_words(keywords)
    top_n = max(1, min(int(top_n or 5), 20))
    if backend_client.enabled():
        return backend_client.call("POST", "/products/keyword-match", source=SOURCE,
                                   body={"keywords": keywords, "base_query": base_query, "top_n": top_n})
    if (blocked := _local_ready()) is not None:
        return blocked
    products = LOCAL.articles()
    scope_note = "H&M 상품 전체"
    if base_query:
        products, parsed = _filter_by_query(products, base_query)
        scope_note = f"'{base_query}' 조건 상품 {len(products)}개 ({'; '.join(KM.mapping_notes(parsed))})"
    result = KM.match_keywords(products, keywords, top_n=top_n)
    result["rows"] = _with_images(result["rows"])
    result["scope"] = scope_note
    ids = list(dict.fromkeys(r["article_id"] for r in result["rows"]))
    return _ok(result, _product_evidence(ids))


# ---------------------------------------------------------------- 3
def analyze_product_sales(article_ids: list[str] | None = None, product_codes: list[str] | None = None,
                          weeks: int = PA.DEFAULT_WEEKS, end_date: str | None = None,
                          channel: str = "online") -> dict:
    """동일 기간·조건의 상품별 구매 실적 비교.

    article_ids(색상별 상품) 또는 product_codes(상품 모델) 중 하나 (최대 30개).
    weeks: 비교 기간 주 수 (기본 4). end_date: 끝 날짜 YYYY-MM-DD (없으면 H&M 마지막 거래일).
    channel: "online"(기본) | "offline" | "all".
    구매 건수·비중·고객 수·직전 같은 기간 대비 증가율·주별 추이를 돌려줍니다.
    """
    channel = channel if channel in LOCAL.CHANNELS else "online"
    key = "product_code" if product_codes and not article_ids else "article_id"
    ids = normalize_ids(article_ids) if key == "article_id" else clean_words(product_codes)
    if backend_client.enabled():
        return backend_client.call("POST", "/products/sales", source=SOURCE, body={
            "article_ids": ids if key == "article_id" else None, "product_codes": ids if key != "article_id" else None,
            "weeks": weeks, "end_date": end_date, "channel": channel})
    if (blocked := _local_ready()) is not None:
        return blocked
    if not ids:
        return _unavailable("비교할 상품 ID가 없습니다 (article_ids 또는 product_codes 필요)")
    dropped = ids[MAX_IDS:]
    ids = ids[:MAX_IDS]
    products = LOCAL.articles()
    info = products[products[key].isin(ids)]
    if key == "product_code":
        info = info.drop_duplicates("product_code")
    unknown = [i for i in ids if i not in set(info[key])]
    end = pd.Timestamp(end_date).date() if end_date else None
    period = PA.make_period(LOCAL.data_as_of(), weeks, end)
    totals, weekly = LOCAL.sales([i for i in ids if i not in unknown], key, period, channel)
    summary = PA.summarize_sales(totals, weekly, info[[key, *[c for c in PA.INFO_COLUMNS if c in info]]], key)
    data = {
        "level": "색상별 상품(article_id)" if key == "article_id" else "상품 모델(product_code, 색상 합산)",
        "period": period, "channel": LOCAL.CHANNEL_LABEL[channel], **summary,
        "definition": "purchases = 구매 기록 행 수(반복 행은 수량으로 봄), growth_rate = 직전 같은 길이 기간 대비",
    }
    if unknown:
        data["unknown_ids"] = unknown
    if dropped:
        data["note"] = f"한 번에 {MAX_IDS}개까지만 비교합니다. 제외: {dropped}"
    evidence = [{"source_type": "hm_sales",
                 "source_id": f"hm_sales:{key}:{period['start']}~{period['end']}:{channel}",
                 "label": f"H&M {LOCAL.CHANNEL_LABEL[channel]} 구매 기록 {period['start']}~{period['end']}"}]
    return _ok(data, evidence)


# ---------------------------------------------------------------- 4
def get_product_details(article_ids: list[str]) -> dict:
    """상품 설명, 소재, 색상, 이미지 등 정보 조회 (article_id 목록, 최대 30개).

    소재·디자인 특징은 상품 설명(detail_desc)에서 규칙으로 찾은 스타일 태그입니다 (H&M에 소재 컬럼은 없음).
    같은 상품 모델(product_code)의 다른 색상도 함께 돌려줍니다.
    """
    ids = normalize_ids(article_ids)[:MAX_IDS]
    if backend_client.enabled():
        rows = [backend_client.call("GET", f"/products/{a}", source=SOURCE) for a in ids]
        ok = [r for r in rows if r.get("available")]
        return {"available": bool(ok), "status": "연결됨 (백엔드)" if ok else (rows[0]["status"] if rows else "ID 없음"),
                "source": SOURCE, "data": {"rows": [r.get("data") for r in ok]},
                "evidence": [e for r in ok for e in r.get("evidence", [])]}
    if (blocked := _local_ready()) is not None:
        return blocked
    products = LOCAL.articles()
    found = products[products["article_id"].isin(ids)]
    rows = []
    for _, row in found.iterrows():
        tags = list(row["style_tags"])
        siblings = products[(products["product_code"] == row["product_code"]) &
                            (products["article_id"] != row["article_id"])]
        rows.append({
            **{c: (None if pd.isna(row[c]) else row[c]) for c in LOCAL.ARTICLE_COLUMNS},
            "style_tags": tags,
            "materials": [t for t in tags if KM.tag_group(t) == "소재"],
            "design_features": [t for t in tags if KM.tag_group(t) in ("디자인", "짜임", "핏", "무늬", "형태")],
            "other_colours": [{"article_id": s["article_id"], "colour_group_name": s["colour_group_name"]}
                              for _, s in siblings.iterrows()],
        })
    rows = _with_images(rows)
    data = {"rows": rows, "unknown_ids": [i for i in ids if i not in set(found["article_id"])],
            "note": "materials·design_features는 상품 설명 문장에서 규칙으로 찾은 값입니다 (H&M에 소재 컬럼 없음)."}
    return _ok(data, _product_evidence(found["article_id"]))


# ---------------------------------------------------------------- 5
def compare_product_candidates(article_ids: list[str], keywords: list[str], weeks: int = PA.DEFAULT_WEEKS,
                               end_date: str | None = None, channel: str = "online") -> dict:
    """후보 상품의 키워드 일치도와 판매 근거 비교.

    article_ids: 후보 상품 (최대 30개). keywords: 비교할 트렌드 키워드.
    weeks·end_date·channel은 analyze_product_sales와 같습니다 (모든 후보에 같은 기간·조건 적용).
    정렬 기준과 기준별 1위(키워드 일치·구매 건수·증가율)를 함께 돌려줍니다. 추천을 단정하지 않습니다.
    """
    ids = normalize_ids(article_ids)[:MAX_IDS]
    keywords = clean_words(keywords)
    if backend_client.enabled():
        return backend_client.call("POST", "/products/compare", source=SOURCE, body={
            "article_ids": ids, "keywords": keywords, "weeks": weeks, "end_date": end_date, "channel": channel})
    if (blocked := _local_ready()) is not None:
        return blocked
    details = get_product_details(ids)
    sales = analyze_product_sales(article_ids=ids, weeks=weeks, end_date=end_date, channel=channel)
    candidates = LOCAL.articles()
    candidates = candidates[candidates["article_id"].isin(ids)]
    keyword_rows, keyword_notes = [], []
    for keyword in keywords:
        scored, parsed = KM.match_keyword(candidates, keyword)
        keyword_notes.append({"keyword": keyword, "mapping_notes": KM.mapping_notes(parsed),
                              "unmatched_terms": parsed["unmatched_terms"]})
        if "match_rate" in scored:
            keyword_rows += [{"article_id": r["article_id"], "keyword": keyword, "match_rate": float(r["match_rate"])}
                             for _, r in scored.iterrows()]
    result = PA.compare_candidates(details["data"]["rows"], keyword_rows, sales["data"].get("rows", []), keywords)
    result["rows"] = _with_images(result["rows"])
    result.update({"keywords": keyword_notes, "period": sales["data"].get("period"),
                   "channel": sales["data"].get("channel"), "unknown_ids": details["data"]["unknown_ids"],
                   "small_sample": sales["data"].get("small_sample", [])})
    return _ok(result, details["evidence"] + sales["evidence"])


PRODUCT_TOOLS = {
    "search_products": search_products,
    "match_product_keywords": match_product_keywords,
    "analyze_product_sales": analyze_product_sales,
    "get_product_details": get_product_details,
    "compare_product_candidates": compare_product_candidates,
}

# Tool → 출처 key (답변의 data_sources 표시용)
TOOL_SOURCE = {name: "hm" for name in PRODUCT_TOOLS}


def data_status() -> dict:
    """연결 상태 (Agent 프롬프트 안내용)."""
    if backend_client.enabled():
        return {"hm": "백엔드 API로 조회"}
    info = LOCAL.status()
    status = info["status"]
    if info["available"]:
        try:
            status += f", 데이터 마지막 날 {LOCAL.data_as_of()}"
        except Exception as exc:  # DB를 열 수 없으면 상태에 표시
            status = f"조회 실패 ({type(exc).__name__})"
        try:
            status += f"; 상품 이미지: {IMAGES.status()['status']}"
        except Exception as exc:
            status += f"; 상품 이미지: 조회 실패 ({type(exc).__name__})"
    return {"hm": status}
