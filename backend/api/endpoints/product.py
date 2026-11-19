"""
backend/api/routes/product.py

Product Analysis Agent — Tool 5개 = API 5개
scope: product:read

의존성은 API별이 아니라 DB 조합별로 2개
- ProductQueryService    : DuckDB만             → search, detail
- ProductAnalysisService : DuckDB + PostgreSQL  → keyword-match, sales, compare

오류(404·403)는 서비스가 도메인 예외로 던지고,
api/error_handlers.py가 HTTP 응답 + run_event 기록으로 바꾼다.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.schemas.common import ToolResult
from backend.schemas.product import (
    ProductSearchData,
    ProductSearchQuery,
    ProductDetailData
)

from backend.api.dependencies import(
    get_analytics_connection
)
from backend.services.product_service import (
    ProductQueryService
)

router = APIRouter(
    prefix="/products",
    tags=["Product"]
    # dependencies=[Depends(req)] # 토큰 확인
)

# --------------------------------------------------
# 1. Service 의존성 (DB 조합별)
# --------------------------------------------------

def get_product_query_service(
        analytics=Depends(get_analytics_connection)
) -> ProductQueryService:
    return ProductQueryService(analytics=analytics)

QueryServiceDep = Annotated[
    ProductQueryService,
    Depends(get_product_query_service)    
]

ArticleIdPath = Annotated[
    str,
    Path(pattern=r"^\d{10}$", description="10자리 article_id")
]

# --------------------------------------------------
# 2. API
# --------------------------------------------------

@router.get(
    "/search",
    response_model=ToolResult[ProductSearchData]
)
def search_products(
    query: Annotated[ProductSearchQuery, Query()],
    service: QueryServiceDep
):
    return service.search_products(query)

@router.get(
    "/{article_id}",
    response_model=ToolResult[ProductDetailData]
)
def get_product_details(
    article_id: ArticleIdPath,
    service: QueryServiceDep
):
    return service.get_product_details(article_id)