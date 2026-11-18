"""
backend/services/product_service.py
② Product Analysis — 조회는 repository, 계산은 shared_analysis, 근거 저장은 evidence_service

  흐름: repository(DuckDB/PG 조회) → shared_analysis(순수 계산) → analysis_snapshot 저장 → ToolResult(data, evidence)

이 파일이 가정하는 인터페이스
  HMProductRepository(analytics)                         [Duck] hm.articles
      .search(keyword, filters: dict[str, list[str]], limit, offset) -> (total, list[dict])
      .get(article_id) -> dict | None
      .get_many(article_ids) -> list[dict]
      .list_color_variants(product_code, exclude) -> list[str]
      .find_by_attributes(attributes: dict[str, list[str]], filters, limit) -> list[dict]
  HMSalesRepository(analytics)                           [Duck] daily_purchases
      .totals(article_ids, start, end, channel_ids) -> [{article_id, qty, buyers, price_index_sum, first_sale_date}]
      .weekly(article_ids, start, end, channel_ids)  -> [{article_id, week_start, qty, buyers}]
  EvidenceService(session)                               [PG] analysis_snapshot
      .save_snapshot(run_id, tool_name, source_type, params, result, data_as_of) -> int
      .load_snapshot(snapshot_id, run_id, tool_name) -> Snapshot(result: dict)
          없으면 NotFoundError(404) / 다른 run이면 PermissionDenied(403) / 다른 Tool 결과면 InvalidRequestError(422)
  keyword_matching.map_keywords(keywords, vocab) -> (mapping: {keyword: [(attribute, value)]}, unmapped: list[str])
  keyword_matching.score(products, mapping) -> [{product, match_score, matched}] (점수 내림차순)
"""

from datetime import timedelta

from backend.core.config import settings
from backend.core.exceptions import InvalidRequestError
from backend.repositories.analytics.hm_product_repository import HMProductRepository
from backend.repositories.analytics.hm_sale_repository import HMSaleRepository
from backend.schemas.common import Evidence, ToolResult
from backend.schemas.product import(
    KeywordMatchData,
    ProductCompareData,
    ProductCompareRequest,
    ProductDetail,
    ProductDetailData,
    ProductFilter,
    ProductKeywordMatchRequest,
    ProductSalesData,
    ProductSalesRequest,
    ProductSearchData,
    ProductSearchQuery,
    ProductSummary,
    SalesPeriod,
)
from backend.services.evidence_service import EvidenceService

DEFAULT_SALES_DAYS = 28
CHANNEL_IDS = {"all": None, "store": [1], "online":[2]} # sales_channel_id (명칭은 추정)

TOOL_KEYWORD_MATCH = "match_product_keywords"
TOOL_SALES = "analyze_product_sales"
TOOL_COMPARE = "compare_product_candidates"
TOOL_TREND = "analyze_trend_sustainability"

def _product_evidence(p: ProductSummary) -> Evidence:
    return Evidence(source_type="hm_product", source_id=p.article_id, label=p.product_name)

# 함수로 관리하는 이유
# 1. 반복되는 객체 생성 코드 재사용
# 2. source_id 형식(snapshot:{snapshot_id})을 일관되게 유지
# 3. 생성 규칙이 변경되면 함수 한 곳만 수정
def _snapshot_evidence(source_type: str, snapshot_id: int, label: str) -> Evidence:
    return Evidence(source_type=source_type, source_id=f"snapshot:{snapshot_id}", label=label)

class ProductService:
    def __init__(self, analytics, session):
        self.session = session
        self.products = HMProductRepository(analytics)
        self.sales = HMSaleRepository(analytics)
        self.evidence = EvidenceService(session)

    # ── search_products ─────────────────────────────────────────────────────
    def search_product(self, query: ProductSearchQuery) -> ToolResult[ProductSearchData]:
        # ProductFilter에 정의된 필드만 추출하여 dict로 변환
        filters = query.model_dump(include=set(ProductFilter.model_fields), exclude_none=True)

        total, rows = self.products.search(
            keyword=query.keyword, filters=filters, limit=query.limit, offset=query.offset
        )
        items = [ProductSummary.model_validate(r) for r in rows]
        return ToolResult(
            data=ProductSearchData(total=total, items=items),
            evidence=[_product_evidence(p) for p in items]
        )

    # ── get_product_details ─────────────────────────────────────────────────
    def get_product_details(self, article_id: str) -> ToolResult[ProductDetailData] | None:
        row = self.products.get(article_id)
        if row is None:
            return None

        product = ProductDetail.model_validate({
            **row,
            "variants": self.products.list_product_variants(row["product_code"], exclude=article_id),
        })

        return ToolResult(
            data=ProductDetailData(product=product),
            evidence=[_product_evidence(product)]
        )

    # ── match_product_keywords ──────────────────────────────────────────────
    def match_product_keywords(
            self,
            request: ProductKeywordMatchRequest,
            run_id: str
    ) -> ToolResult[KeywordMatchData]:
        evidence: list[Evidence] = []
        if request.trend_snapshot_id is not None:
            trend = self.evidence.load_snapshot(request.trend_snapshot_id, run_id=run_id, expected_tool_name=TOOL_TREND)
            keywords = list(trend.result["keywords"])
            evidence.append(_snapshot_evidence("trend", request.trend_snapshot_id, "트랜드 키워드"))
        else:
            keywords = request.keywords

