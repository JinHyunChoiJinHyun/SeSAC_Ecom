"""① Market Analysis Agent Tool 6개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    get_shopping_trends           네이버 쇼핑인사이트 카테고리·키워드 검색 추이 조회
    analyze_trend_sustainability  관심 상승률 및 기획전 기간까지의 지속 가능성 분석
    analyze_youtube_trends        유튜브 관련 영상 조회수·게시일·키워드 분석
    analyze_competitor_reviews    무신사 유사 상품 리뷰의 언급률·불만 비율 분석
    analyze_selling_points        경쟁 상품명과 리뷰에서 소재·핏·관리·디자인 소구점 추출
    search_fashion_trends         셀럽 착장 및 패션 매체의 트렌드 근거 검색

동작 모드 (아키텍처 [3] Tool → API → Service)
- 백엔드 모드: CART_SIGNAL_BACKEND_URL이 있으면 backend_client로 아래 API를 호출합니다 (팀원 백엔드 준비 후).
      GET  /market/shopping-trends       POST /market/trend-sustainability   POST /market/youtube
      POST /market/competitor-reviews    POST /market/selling-points          GET  /market/fashion-trends
- 로컬 모드(지금): 백엔드가 없으면
      무신사 2개 Tool → 샘플 CSV + shared_analysis 계산 (백엔드와 같은 계산 함수)
      네이버·유튜브·패션 매체 Tool → '미연결' (수집 데이터는 백엔드 PostgreSQL external에서 제공 예정)
모든 Tool은 ToolResult를 반환합니다: {"available", "status", "source", "data", "evidence"}
    data      계산 결과 (rows · basis · notes …)
    evidence  근거 목록 [{"source_type", "source_id", "label"}]  (아키텍처 schemas/common.py 형식)
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.common_tools.external_data import musinsa_sample
from shared_analysis.market import review_analysis, selling_points
from shared_analysis.market.common import clean_words

SOURCES = {
    "naver": "네이버 쇼핑인사이트",
    "youtube": "유튜브 Data API",
    "musinsa": "무신사 (랭킹·리뷰)",
    "fashion_media": "패션 매체·셀럽 착장",
}
NOT_CONNECTED = "미연결 (백엔드 연결 전: 수집 데이터는 백엔드 PostgreSQL external에서 제공 예정)"


def _unavailable(source_key: str, status: str = NOT_CONNECTED, **data) -> dict:
    return {"available": False, "status": status, "source": SOURCES[source_key], "data": data, "evidence": [],
            "note": "데이터가 없어 계산하지 않았습니다. 숫자·근거를 추정해서 채우지 마십시오."}


def _musinsa_local(calc, keywords, exclude, **kwargs) -> dict:
    info = musinsa_sample.status()
    if not info["available"]:
        return _unavailable("musinsa", info["status"], keywords=keywords)
    data = musinsa_sample.load()
    result = calc(data["products"], data["reviews"], keywords, exclude, **kwargs)
    result["collected_at"] = data["collected_at"]
    return {"available": True, "status": info["status"], "source": SOURCES["musinsa"] + " 샘플", "data": result,
            "evidence": [{"source_type": "external_review", "source_id": f"musinsa_sample:{data['collected_at']}",
                          "label": f"무신사 리뷰 샘플 ({data['collected_at']})"}]}


# ---------------------------------------------------------------- 1
def get_shopping_trends(keywords: list[str], start_date: str | None = None, end_date: str | None = None) -> dict:
    """네이버 쇼핑인사이트 카테고리·키워드 검색 추이 조회. keywords: 키워드 목록, 날짜는 YYYY-MM-DD(선택)."""
    keywords = clean_words(keywords)
    if backend_client.enabled():
        return backend_client.call("GET", "/market/shopping-trends", source=SOURCES["naver"],
                                   params={"keywords": keywords, "start_date": start_date, "end_date": end_date})
    return _unavailable("naver", keywords=keywords)


# ---------------------------------------------------------------- 2
def analyze_trend_sustainability(keywords: list[str], base_keyword: str | None = None,
                                 campaign_start: str | None = None, campaign_end: str | None = None) -> dict:
    """관심 상승률 및 기획전 기간까지의 지속 가능성 분석. base_keyword: 비교 기준어(예: 니트), 기간은 YYYY-MM-DD."""
    keywords = clean_words(keywords)
    if backend_client.enabled():
        return backend_client.call("POST", "/market/trend-sustainability", source=SOURCES["naver"], body={
            "keywords": keywords, "base_keyword": base_keyword,
            "campaign_start": campaign_start, "campaign_end": campaign_end})
    return _unavailable("naver", keywords=keywords)


# ---------------------------------------------------------------- 3
def analyze_youtube_trends(keywords: list[str], base_keyword: str | None = None, recent_days: int | None = None) -> dict:
    """유튜브 관련 영상 조회수·게시일·키워드 분석 (최근 게시 영상의 하루 평균 조회수, 기준어 대비 배율)."""
    keywords = clean_words(keywords)
    if backend_client.enabled():
        return backend_client.call("POST", "/market/youtube", source=SOURCES["youtube"], body={
            "keywords": keywords, "base_keyword": base_keyword, "recent_days": recent_days})
    return _unavailable("youtube", keywords=keywords)


# ---------------------------------------------------------------- 4
def analyze_competitor_reviews(keywords: list[str], exclude: list[str] | None = None) -> dict:
    """무신사 유사 상품 리뷰의 언급률·불만 비율 분석. '하프집업 니트'처럼 띄어 쓰면 두 단어가 모두 든 상품."""
    keywords, exclude = clean_words(keywords), clean_words(exclude)
    if backend_client.enabled():
        return backend_client.call("POST", "/market/competitor-reviews", source=SOURCES["musinsa"],
                                   body={"keywords": keywords, "exclude": exclude})
    return _musinsa_local(review_analysis.analyze_reviews, keywords, exclude)


# ---------------------------------------------------------------- 5
def analyze_selling_points(keywords: list[str], exclude: list[str] | None = None, include_reviews: bool = True) -> dict:
    """경쟁 상품명과 리뷰에서 소재·핏·관리·디자인 소구점 추출 (표현 등장 비율)."""
    keywords, exclude = clean_words(keywords), clean_words(exclude)
    if backend_client.enabled():
        return backend_client.call("POST", "/market/selling-points", source=SOURCES["musinsa"],
                                   body={"keywords": keywords, "exclude": exclude, "include_reviews": include_reviews})
    return _musinsa_local(selling_points.analyze_selling_points, keywords, exclude, include_reviews=include_reviews)


# ---------------------------------------------------------------- 6
def search_fashion_trends(query: str, limit: int = 10) -> dict:
    """셀럽 착장 및 패션 매체의 트렌드 근거 검색 (출처 링크·날짜 포함)."""
    query = str(query or "").strip()
    if backend_client.enabled():
        return backend_client.call("GET", "/market/fashion-trends", source=SOURCES["fashion_media"],
                                   params={"query": query, "limit": limit})
    return _unavailable("fashion_media", query=query)


MARKET_TOOLS = {
    "get_shopping_trends": get_shopping_trends,
    "analyze_trend_sustainability": analyze_trend_sustainability,
    "analyze_youtube_trends": analyze_youtube_trends,
    "analyze_competitor_reviews": analyze_competitor_reviews,
    "analyze_selling_points": analyze_selling_points,
    "search_fashion_trends": search_fashion_trends,
}

# Tool → 출처 key (답변의 data_sources 표시용)
TOOL_SOURCE = {"get_shopping_trends": "naver", "analyze_trend_sustainability": "naver",
               "analyze_youtube_trends": "youtube", "analyze_competitor_reviews": "musinsa",
               "analyze_selling_points": "musinsa", "search_fashion_trends": "fashion_media"}


def data_status() -> dict:
    """출처별 연결 상태 (Agent 프롬프트 안내용)."""
    if backend_client.enabled():
        return {key: "백엔드 API로 조회" for key in SOURCES}
    return {"naver": NOT_CONNECTED, "youtube": NOT_CONNECTED,
            "musinsa": musinsa_sample.status()["status"], "fashion_media": NOT_CONNECTED}
