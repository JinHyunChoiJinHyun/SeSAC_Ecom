"""시장 분석 Tool 5개. 로컬 시장 데이터와 공통 분석 함수를 직접 사용한다."""

from __future__ import annotations

from agent_system.common_tools.external_data import musinsa_sample, trend_files
from shared_analysis.market import review_analysis, selling_points, trend_analysis, youtube_analysis
from shared_analysis.market.common import clean_words

SOURCES = {
    "naver": "네이버 쇼핑인사이트",
    "youtube": "유튜브 Data API",
    "musinsa": "무신사 (랭킹·리뷰)",
}
NOT_CONNECTED = "미연결 (로컬 수집 데이터 없음)"


def _unavailable(source_key: str, status: str = NOT_CONNECTED, **data) -> dict:
    return {"available": False, "status": status, "source": SOURCES[source_key], "data": data, "evidence": [],
            "note": "데이터가 없어 계산하지 않았습니다. 숫자·근거를 추정해서 채우지 마십시오."}


def _musinsa_local(calc, keywords, exclude, **kwargs) -> dict:
    info = musinsa_sample.status()
    if not info["available"]:
        return _unavailable("musinsa", info["status"], keywords=keywords)
    data = musinsa_sample.load()
    # CART_SIGNAL_OUTER_BRIDGE_V1
    # 네이버 '경량패딩' 관심 추이와 무신사 '아우터' 상품·리뷰를 연계합니다.
    # 아우터 전체를 경량패딩으로 간주하지 않고, 상품명의 유관 표현만 대상으로 삼습니다.
    selected_products = data["products"]
    selected_reviews = data["reviews"]
    mapped_keywords = keywords
    outer_bridge = len(keywords) == 1 and keywords[0].replace(" ", "") == "경량패딩"
    if outer_bridge:
        if "category" in selected_products.columns:
            selected_products = selected_products.loc[
                selected_products["category"].fillna("").astype(str).eq("아우터")
            ].copy()
            ids = set(selected_products["item_id"].astype(str))
            selected_reviews = selected_reviews.loc[
                selected_reviews["item_id"].astype(str).isin(ids)
            ].copy()
        else:
            # 카테고리 정보가 없으면 임의로 전체 상품군을 아우터로 취급하지 않음
            selected_products = selected_products.iloc[0:0].copy()
            selected_reviews = selected_reviews.iloc[0:0].copy()
        mapped_keywords = ["경량패딩", "경량 패딩", "경량 다운", "라이트 다운",
                           "라이트다운", "씬라이트", "라이트 패딩", "라이트패딩"]
    result = calc(selected_products, selected_reviews, mapped_keywords, exclude, **kwargs)
    if outer_bridge:
        result["category_mapping"] = {
            "requested_keyword": "경량패딩", "source_category": "아우터",
            "matched_name_keywords": mapped_keywords,
            "note": "상품명에 경량 관련 표현이 포함된 아우터만 집계했습니다. "
                    "표현 매칭은 경량성의 실제 검증이 아닙니다. 네이버 관심 수치와 무신사 리뷰 지표는 합산하지 않습니다.",
        }

    result["collected_at"] = data["collected_at"]
    result["categories"] = data.get("categories", [])
    return {"available": True, "status": info["status"], "source": SOURCES["musinsa"] + " (로컬 CSV)", "data": result,
            "evidence": [{"source_type": "external_review", "source_id": f"musinsa_sample:{data['collected_at']}",
                          "label": f"무신사 랭킹·리뷰 ({data['collected_at']})"}]}


def _naver_local(calc, keywords, **kwargs) -> dict:
    info = trend_files.naver_status()
    if not info["available"]:
        return _unavailable("naver", info["status"], keywords=keywords)
    series = trend_files.load_naver()
    result = calc(series, keywords, **kwargs)
    result["available_keywords"] = info["keywords"]
    found = [r for r in result.get("rows") or [] if r.get("periods")]
    if not found:
        return _unavailable("naver", "데이터 없음 (요청 키워드가 네이버 수집 파일에 없음)", keywords=keywords,
                            available_keywords=info["keywords"])
    latest = max(str(r.get("latest_period") or "") for r in found)
    result["collected_at"] = latest
    return {"available": True, "status": info["status"], "source": SOURCES["naver"] + " (로컬 배치 CSV)",
            "data": result,
            "evidence": [{"source_type": "trend", "source_id": f"naver_local:{r['keyword']}:{r.get('latest_period')}",
                          "label": f"네이버 쇼핑인사이트 '{r['keyword']}' (~{r.get('latest_period')})"} for r in found]}


def _youtube_local(keywords, base_keyword, recent_days) -> dict:
    info = trend_files.youtube_status()
    if not info["available"]:
        return _unavailable("youtube", info["status"], keywords=keywords)
    data = trend_files.load_youtube()
    if not data["videos"].empty:
        result = youtube_analysis.analyze_videos(data["videos"], keywords, base_keyword, recent_days)
        kind = "영상 지표"
    else:
        result = youtube_analysis.summarize_candidates(data["candidates"], keywords, base_keyword)
        kind = "키워드 후보"
    result["files"] = data["files"]
    if not any(r.get("channel_count") or r.get("recent_videos") for r in result.get("rows") or []):
        result["note"] = "요청 키워드가 유튜브 수집 파일에 없습니다. top_candidates(등장 채널 수 상위)를 참고하십시오."
    stamp = result.get("collected_at") or next((r.get("as_of") for r in result.get("rows") or [] if r.get("as_of")), "")
    return {"available": True, "status": info["status"], "source": SOURCES["youtube"] + f" (로컬 배치 CSV, {kind})",
            "data": result,
            "evidence": [{"source_type": "youtube", "source_id": f"youtube_local:{f}", "label": f"유튜브 {kind} {f}"}
                         for f in data["files"]] if stamp or data["files"] else []}


# ---------------------------------------------------------------- 1
def get_shopping_trends(keywords: list[str], start_date: str | None = None, end_date: str | None = None) -> dict:
    """네이버 쇼핑인사이트 카테고리·키워드 검색 추이 조회. keywords: 키워드 목록, 날짜는 YYYY-MM-DD(선택)."""
    keywords = clean_words(keywords)
    return _naver_local(trend_analysis.summarize_trends, keywords, start_date=start_date, end_date=end_date)


# ---------------------------------------------------------------- 2
def analyze_trend_sustainability(keywords: list[str], base_keyword: str | None = None,
                                 campaign_start: str | None = None, campaign_end: str | None = None) -> dict:
    """관심 상승률 및 기획전 기간까지의 지속 가능성 분석. base_keyword: 비교 기준어(예: 니트), 기간은 YYYY-MM-DD."""
    keywords = clean_words(keywords)
    # CART_SIGNAL_CAMPAIGN_DATE_VALIDATION_V1
    # LLM이 잘못 생성한 날짜를 Tool 성공으로 간주하지 않습니다.
    from datetime import date
    if campaign_start is not None or campaign_end is not None:
        if not (campaign_start and campaign_end):
            raise ValueError("기획전 시작일과 종료일을 함께 입력해 주세요.")
        try:
            start_day = date.fromisoformat(campaign_start)
            end_day = date.fromisoformat(campaign_end)
        except (TypeError, ValueError) as exc:
            raise ValueError("기획전 날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
        if end_day <= start_day:
            raise ValueError(
                f"기획전 기간 오류: 시작 {campaign_start}, 종료 {campaign_end}. "
                "종료일은 시작일보다 늦어야 합니다. 원래 마케터 요청의 날짜를 확인하세요."
            )
    return _naver_local(trend_analysis.analyze_sustainability, keywords, base_keyword=base_keyword,
                        campaign_start=campaign_start, campaign_end=campaign_end)


# ---------------------------------------------------------------- 3
def analyze_youtube_trends(keywords: list[str], base_keyword: str | None = None, recent_days: int | None = None) -> dict:
    """유튜브 관련 영상 조회수·게시일·키워드 분석 (최근 게시 영상의 하루 평균 조회수, 기준어 대비 배율)."""
    keywords = clean_words(keywords)
    return _youtube_local(keywords, base_keyword, recent_days)


# ---------------------------------------------------------------- 4
def analyze_competitor_reviews(keywords: list[str], exclude: list[str] | None = None) -> dict:
    """무신사 유사 상품 리뷰의 언급률·불만 비율 분석. '하프집업 니트'처럼 띄어 쓰면 두 단어가 모두 든 상품."""
    keywords, exclude = clean_words(keywords), clean_words(exclude)
    return _musinsa_local(review_analysis.analyze_reviews, keywords, exclude)


# ---------------------------------------------------------------- 5
def analyze_selling_points(keywords: list[str], exclude: list[str] | None = None, include_reviews: bool = True) -> dict:
    """경쟁 상품명과 리뷰에서 소재·핏·관리·디자인 소구점 추출 (표현 등장 비율)."""
    keywords, exclude = clean_words(keywords), clean_words(exclude)
    return _musinsa_local(selling_points.analyze_selling_points, keywords, exclude, include_reviews=include_reviews)




MARKET_TOOLS = {
    "get_shopping_trends": get_shopping_trends,
    "analyze_trend_sustainability": analyze_trend_sustainability,
    "analyze_youtube_trends": analyze_youtube_trends,
    "analyze_competitor_reviews": analyze_competitor_reviews,
    "analyze_selling_points": analyze_selling_points,
}

# Tool → 출처 key (답변의 data_sources 표시용)
TOOL_SOURCE = {"get_shopping_trends": "naver", "analyze_trend_sustainability": "naver",
               "analyze_youtube_trends": "youtube", "analyze_competitor_reviews": "musinsa",
               "analyze_selling_points": "musinsa"}


def data_status() -> dict:
    """출처별 연결 상태 (Agent 프롬프트 안내용)."""
    return {"naver": trend_files.naver_status()["status"], "youtube": trend_files.youtube_status()["status"],
            "musinsa": musinsa_sample.status()["status"]}
