"""
아래 Tool에 대응하는 요청/응답 스키마
Tool                         API                            요청                        응답 data
search_products              GET  /products/search          ProductSearchQuery          ProductSearchData
get_product_details          GET  /products/{article_id}    (path: ArticleId)           ProductDetailData
match_product_keywords       POST /products/keyword-match   ProductKeywordMatchRequest  KeywordMatchData
analyze_product_sales        POST /products/sales           ProductSalesRequest         ProductSalesData
compare_product_candidates   POST /products/compare         ProductCompareRequest       ProductCompareData
"""
from typing import Annotated, Literal
from datetime import date
from pydantic import Field, BaseModel, ConfigDict, model_validator, field_validator

# from backend.core.config import settings

# H&M 상품 ID 타입
# 사용 시점: 상품 검색, 상세 조회, 판매 분석 등에서 상품 ID를 입력받거나 반환할 때
# 10자리 문자열 형식을 공통으로 검증한다.
ArticleId = Annotated[
    str,
    # 검증 조건
    Field(pattern=r"^\d{10}$", description="H&M article_id. 앞자리 0을 포함한 10자리 문자열")
]

# 분석 결과 스냅샷 ID 타입
# 사용 시점: 키워드 매칭, 판매 분석 등의 결과를 저장한 뒤
# 다른 Tool에서 해당 분석 결과를 다시 참조할 때 사용한다.
SnapshotId = Annotated[
    int,
    Field(gt=0)
]

# 판매 채널 타입
# 사용 시점: 상품 판매 분석 요청 시 전체·매장·온라인 중 분석 대상을 지정할 때 사용한다.
Channel = Literal["all", "store", "online"]

# 모든 API 요청 스키마의 공통 부모 클래스
# 사용 시점: 각 Tool의 요청 데이터를 검증할 때 상속받아 사용한다.
# 정의되지 않은 요청 필드가 들어오면 오류(422)를 반환한다.
class _Request(BaseModel):
    """정의되지 않은 필드 입력 시 422 오류 반환"""
    model_config = ConfigDict(extra="forbid")

def _unique(ids: list[str]) -> list[str]:
    """중복 ID 제거 리스트 반환"""
    return list(dict.fromkeys(ids)) # key는 중복될 수 없으므로 dict로 변환 시 중복 제거

# 상품 검색에 사용할 공통 필터 스키마
# 사용 시점:
# ① search_products: 상품 검색 조건을 입력받을 때
# ② match_product_keywords: 키워드 매칭 대상 상품의 범위를 제한할 때
#
# H&M 상품의 부문, 상품 분류, 색상, 무늬 등의 조건을 지정한다.
class ProductFilter(_Request):
    index_groups: list[str] | None = Field(
        None, max_length=5,
        description="최상위 부문 (index_group_name, 5종): Ladieswear, Baby/Children, Divided, Menswear, Sport",
    )
    product_groups: list[str] | None = Field(
        None, max_length=10, description="상위 상품 분류 (product_group_name, 19종). 예: Garment Upper body"
    )
    product_types: list[str] | None = Field(
        None, max_length=20, description="세부 상품 유형 (product_type_name, 131종). 예: Sweater, Vest top"
    )
    garment_groups: list[str] | None = Field(
        None, max_length=10, description="의류군 (garment_group_name, 21종). 예: Knitwear, Jersey Basic"
    )
    colors: list[str] | None = Field(
        None, max_length=10, description="세부 색상 (colour_group_name, 50종). 예: Off White"
    )
    color_masters: list[str] | None = Field(
        None, max_length=10, description="대표 색상 계열 (perceived_colour_master_name). 색 계열 단위로 찾을 때"
    )
    tones: list[str] | None = Field(
        None, max_length=5, description="밝기·톤 (perceived_colour_value_name). 예: Dark, Light, Dusty Light"
    )
    appearances: list[str] | None = Field(
        None, max_length=10, description="무늬·외관 (graphical_appearance_name, 30종). 예: Solid"
    )

    def has_any_filter(self) -> bool:
        """필터 입력 여부 확인"""
        return any(getattr(self, f) for f in ProductFilter.model_fields)

# 상품 목록에 표시할 기본 정보 스키마
# 사용 시점:
# ① search_products: 검색 결과에 포함할 상품 정보
# ② match_product_keywords: 키워드와 매칭된 상품 정보
# ③ compare_product_candidates: 비교 대상 상품의 기본 정보
#
# 상세 설명(detail_desc)처럼 데이터가 길어질 수 있는 정보는 제외한다.
class ProductSummary(BaseModel):
    """상품 목록용 스키마"""
    article_id: ArticleId
    product_code: str = Field(description="상품 모델 코드 (product_code, 예: 0108775). 색상 변형을 묶는 키")
    product_name: str = Field(description="prod_name (영문)")
    product_type: str = Field(description="product_type_name")
    product_group: str = Field(description="product_group_name")
    index_group: str = Field(description="index_group_name")
    section: str = Field(description="section_name")
    department: str = Field(description="department_name")
    garment_group: str = Field(description="garment_group_name")
    color: str = Field(description="colour_group_name")
    color_master: str = Field(description="perceived_colour_master_name")
    tone: str = Field(description="perceived_colour_value_name")
    appearance: str = Field(description="graphical_appearance_name")
    # image_location: str | None = Field(None, description="hm.article_images 경로. 이미지 없는 상품이 있다")

# 상품 상세 정보 스키마
# 사용 시점: get_product_details에서 특정 상품의 상세 정보를 반환할 때 사용한다.
# ProductSummary의 기본 정보에 상세 설명과 동일 상품의 색상 변형 정보를 추가한다.
class ProductDetail(ProductSummary):
    detail_desc: str | None = Field(
        None, description="원문 그대로 (영문, 416건 결측). 소재·핏 정보는 별도 컬럼 없이 이 텍스트에만 있다"
    )
    variants: list[ArticleId] = Field(default_factory=list, description="같은 product_code의 다른 색상")

# ──────────────────────────────────────────────────────────────────────────────
# search_products   GET /products/search
# ──────────────────────────────────────────────────────────────────────────────

# 상품 검색 API의 요청 스키마
# 사용 시점: 마케터 또는 에이전트가 상품명이나 상품 속성으로 상품을 검색할 때 사용한다.
# ProductFilter를 상속하여 검색 조건을 받고, 페이지네이션 조건도 함께 지정한다.
class ProductSearchQuery(ProductFilter):
    keyword: str | None = Field(None, min_length=1, max_length=50, description="상품명·detail_desc 부분 일치")
    filters: dict[str, list[str]] = Field(
        default_factory=dict,
        description="상품 검색 필터"
    )
    limit: int = Field(20, ge=1, le=50)
    offset: int = Field(0, ge=0)

    @model_validator(mode="after")
    def validate_search_condition(self):
        """불필요한 전체 조회 방지를 위해 키워드 혹은 필터 입력 강제"""
        if not self.keyword and not self.has_any_filter():
            raise ValueError("keyword 또는 필터를 하나 이상 지정해야 합니다.")
        return self # 모델 객체 반환

# 상품 검색 API의 응답 데이터 스키마
# 사용 시점: search_products 실행 후 검색된 상품 목록과 전체 검색 결과 수를 반환할 때 사용한다.
class ProductSearchData(BaseModel):
    total: int
    items: list[ProductSummary]

# ──────────────────────────────────────────────────────────────────────────────
# get_product_details   GET /products/{article_id}
# ──────────────────────────────────────────────────────────────────────────────

# 상품 상세 조회 API의 응답 데이터 스키마
# 사용 시점: get_product_details 실행 후 특정 상품의 상세 정보와 이미지 경로를 반환할 때 사용한다.
class ProductDetailData(BaseModel):
    product: ProductDetail
    # images: list[str] = Field(default_factory=list, description="image_location 목록")

# ──────────────────────────────────────────────────────────────────────────────
# match_product_keywords   POST /products/keyword-match
#   트렌드 키워드([PG]) × product_features([Duck]) → shared_analysis/product/keyword_matching.py
# ──────────────────────────────────────────────────────────────────────────────

# 트렌드 키워드와 상품 속성의 매칭 API 요청 스키마
# 사용 시점: match_product_keywords 실행 시 사용한다.
# 트렌드 분석 스냅샷 또는 마케터가 직접 입력한 키워드 중 하나를 전달한다.
# ProductFilter를 상속하므로 매칭 대상 상품의 범위도 제한할 수 있다.
# class ProductKeywordMatchRequest(ProductFilter):
#     trend_snapshot_id: SnapshotId | None = Field(
#         None,
#         description=(
#             "analyze_trend_sustainability 결과. 서버가 이 스냅샷에서 카테고리와 키워드를 함께 읽는다. "
#             "트렌드 분석을 하지 않아 스냅샷이 없으면 비워 둔다. "
#             "이때 카테고리는 마케터가 직접 말한 경우에만 category에 넣는다"
#         )
#     )
#     category: CategoryName | None = Field(
#         None, 
#         description=(
#             "마케터가 대화에서 '니트로 해 줘'처럼 카테고리를 직접 말했으면 여기에 '니트'를 넣어라. "
#             "트렌드 분석 결과를 쓰는 경우라면 여기에는 아무것도 넣지 말고(null), 대신 trend_snapshot_id에 스냅샷 번호를 넣어라. "
#             "마케터가 카테고리를 말하지 않았고 트렌드 결과도 없으면 추측해서 넣지 말고 비워 둬라. "
#             "비워 두면 카테고리 제한 없이 키워드에 맞는 상품을 찾는다."
#         ),
#     )
#     keywords: list[str] | None = Field(
#         None,
#         min_length=1,
#         max_length=30,
#         description=(
#             "마케터가 직접 입력한 키워드일 때만 사용. "
#             "트렌드 분석에서 나온 키워드는 여기에 옮겨 적지 말고 trend_snapshot_id를 넘길 것"
#         )
#     )
#     limit: int = Field(20, ge=1, le=50)

#     @model_validator(mode="after")
#     def validate_keyword_source(self):
#         """키워드 출처는 트렌드 스냅샷과 마케터 입력 중 정확히 하나"""
#         if (self.trend_snapshot_id is None) == (self.keywords is None):
#             raise ValueError("trend_snapshot_id와 keywords 중 하나만 지정해야 합니다")
#         return self

#     @model_validator(mode="after")
#     def validate_category_source(self):
#         """트렌드 스냅샷을 쓰면 카테고리도 스냅샷에서 읽는다 (category는 마케터 입력 경로에서만)"""
#         if self.trend_snapshot_id is not None and self.category is not None:
#             raise ValueError("트렌드 결과를 쓸 때는 카테고리도 스냅샷에서 읽습니다. category를 비우세요")
#         return self

# # 키워드와 실제 상품 속성이 어떻게 연결되었는지 나타내는 스키마
# # 사용 시점: 키워드 매칭 결과에서 개별 키워드의 매칭 근거를 반환할 때 사용한다.
# # 예: 퍼플 → color → Purple
# class KeywordHit(BaseModel):
#     keyword: str = Field(description="입력 키워드 (예: 퍼플)")
#     attribute: Literal[
#         "product_group", "product_type", "garment_group", "color", "color_master", "tone", "appearance"
#     ]
#     value: str = Field(description="매칭된 H&M 정식 값 (hm_vocab.json에 있는 값)")

# # 키워드 매칭 결과에 포함되는 개별 상품 정보
# # 사용 시점: 특정 상품의 키워드 매칭 점수와 매칭된 속성 정보를 반환할 때 사용한다.
# class KeywordMatchItem(BaseModel):
#     product: ProductSummary
#     match_score: float = Field(ge=0, le=1)
#     matched: list[KeywordHit]

# # 키워드 매칭 API의 응답 데이터 스키마
# # 사용 시점: match_product_keywords 실행 후 전체 매칭 결과를 반환할 때 사용한다.
# # 분석 결과는 스냅샷으로 저장하고 snapshot_id를 반환하여 후속 Tool에서 참조할 수 있다.
# class KeywordMatchData(BaseModel):
#     snapshot_id: int
#     keywords: list[str]
#     unmapped_keywords: list[str] = Field(description="H&M 속성 사전에 대응 값이 없는 키워드 (예: 꾸안꾸)")
#     keyword_count: int = Field(description="중복을 뺀 입력 키워드 수")
#     mapped_keyword_count: int = Field(description="H&M 속성으로 해석된 키워드 수")
#     resolution_rate: float = Field(
#         ge=0, le=1,
#         description="해석 성공률. match_score는 해석된 키워드만 기준이므로 이 값과 함께 해석할 것",
#     )
#     items: list[KeywordMatchItem]

# # ──────────────────────────────────────────────────────────────────────────────
# # analyze_product_sales   POST /products/sales   ([Duck] daily_purchases)
# # ──────────────────────────────────────────────────────────────────────────────

# # 상품 판매 분석 기간을 지정하는 스키마
# # 사용 시점: ProductSalesRequest에서 분석 시작일과 종료일을 지정할 때 사용한다.
# # H&M 데이터가 존재하는 기간 안에서만 조회할 수 있도록 검증한다.
# class SalesPeriod(_Request):
#     start_date: date
#     end_date: date

#     @model_validator(mode="after")
#     def _within_hm_data(self):
#         if self.start_date > self.end_date:
#             raise ValueError("start_date가 end_date보다 늦습니다")
#         if self.start_date < settings.HM_DATA_START or self.end_date > settings.HM_DATA_REFERENCE_DATE:
#             raise ValueError(
#                 f"H&M 데이터 기간({settings.HM_DATA_START} ~ {settings.HM_DATA_REFERENCE_DATE}) 안에서만 조회할 수 있습니다. "
#                 "오늘 날짜가 아니라 분석 기준일을 기준으로 지정하세요"
#             )

#         return self

# # 상품 판매 분석 API의 요청 스키마
# # 사용 시점: analyze_product_sales 실행 시 사용한다.
# # 분석할 상품, 분석 기간, 판매 채널, 집계 단위를 전달한다.
# # 기간을 생략하면 서비스에서 기준일을 바탕으로 기본 분석 기간을 설정한다.
# class ProductSalesRequest(_Request):
#     article_ids: list[ArticleId] = Field(min_length=1, max_length=50)
#     period: SalesPeriod | None = Field(None, description="생략하면 HM_DATA_REFERENCE_DATE 기준 최근 28일 (서비스에서 결정)")
#     channel: Channel = "all"
#     granularity: Literal["total", "week"] = "total"

#     @field_validator("article_ids") # article_ids 필드에 값 입력 시
#     @classmethod
#     def _remove_duplicate_ids(cls, ids: list[str]) -> list[str]:
#         return _unique(ids)

# # 개별 상품의 판매 지표 스키마
# # 사용 시점: 상품별 판매량, 구매 고객 수, 가격 지표 등
# # 판매 분석 결과의 수치 데이터를 반환할 때 사용한다.
# class SalesMetrics(BaseModel):
#     """매출 지표 스키마 모델"""
#     qty: int = Field(description="daily_purchases.qty 합계 (반복 행 = 수량)")
#     buyers: int = Field(description="고유 구매 고객 수")
#     price_index_sum: float = Field(description="정규화 가격 지표 합. 통화 금액이 아니므로 원화·매출로 표기 금지")
#     first_sale_date: date = Field(
#         description="기간 안에서 처음 판매된 날 (관측값. 출시일이 아님)"
#     )
#     qty_per_day: float = Field(
#         description="qty ÷ (first_sale_date ~ 기간 종료일 일수). 판매 시작 시점이 다른 상품 비교용"
#     )

# # 주간 판매 추이를 나타내는 스키마
# # 사용 시점: ProductSalesRequest의 granularity가 "week"일 때
# # 주별 판매량과 구매 고객 수를 반환하는 데 사용한다.
# class SalesPoint(BaseModel):
#     week_start: date
#     qty: int
#     buyers: int

# # 판매 분석 결과에 포함되는 개별 상품 정보
# # 사용 시점: analyze_product_sales 실행 후 상품별 판매 지표와 순위를 반환할 때 사용한다.
# # 주간 분석을 요청한 경우 주별 판매 추이도 포함한다.
# class ProductSalesItem(BaseModel):
#     article_id: ArticleId
#     metrics: SalesMetrics
#     rank_by_qty_per_day: int
#     series: list[SalesPoint] | None = None

# # 상품 판매 분석 API의 응답 데이터 스키마
# # 사용 시점: analyze_product_sales 실행 후 전체 판매 분석 결과를 반환할 때 사용한다.
# # 실제 적용된 분석 기간, 판매 채널, 상품별 판매 지표, 해석 주의사항을 포함한다.
# class ProductSalesData(BaseModel):
#     snapshot_id: int
#     period: SalesPeriod  # 서비스가 실제 적용한 기간 (기본값 포함)
#     channel: Channel
#     reference_date: date
#     items: list[ProductSalesItem]
#     notes: list[str] = Field(
#         default_factory=list,
#         description=(
#             "해석 주의: 2018-09·2020-09 부분 월, 채널 1·2 명칭은 추정, "
#             "2020-03-20~05-05 채널 1(매장) 공백, 2018~2020 과거 데이터"
#         ),
#     )

# # ──────────────────────────────────────────────────────────────────────────────
# # compare_product_candidates   POST /products/compare   (앞 결과 조합)
# # ──────────────────────────────────────────────────────────────────────────────

# # 상품 후보 비교 API의 요청 스키마
# # 사용 시점: compare_product_candidates 실행 시 사용한다.
# # 앞서 실행한 키워드 매칭 또는 판매 분석 결과의 snapshot_id를 전달하여
# # 기존 분석 결과를 바탕으로 여러 상품을 비교한다.
# class ProductCompareRequest(_Request):
#     article_ids: list[ArticleId] = Field(min_length=2, max_length=10)
#     keyword_match_snapshot_id: SnapshotId | None = None # match_product_keywords에서 생성한 키워드 매칭 분석 결과
#     sales_snapshot_id: SnapshotId | None = None # analyze_product_sales에서 생성한 판매 분석 결과

#     @field_validator("article_ids") # article_ids 필드에 값 입력 시
#     @classmethod
#     def _remove_duplicate_ids(cls, ids: list[str]) -> list[str]:
#         return _unique(ids)

#     @model_validator(mode="after")
#     def _validate_comparison_requirements(self):
#         if len(self.article_ids) < 2:
#             raise ValueError("서로 다른 상품을 2개 이상 지정해야 합니다.")

#         # 분석 결과 여부 검증
#         if self.keyword_match_snapshot_id is None and self.sales_snapshot_id is None:
#             raise ValueError("keyword_match_snapshot_id 또는 sales_snapshot_id 중 하나 이상이 필요합니다")

#         return self

# # 상품 후보 비교 결과에 포함되는 개별 상품 정보
# # 사용 시점: 비교 대상 상품별 키워드 매칭 결과와 판매 지표를 함께 반환할 때 사용한다.
# # 매칭 정보나 판매 분석 정보가 없는 경우 해당 값은 None으로 반환할 수 있다.
# class CompareRow(BaseModel):
#     product: ProductSummary
#     match_score: float | None = None
#     matched: list[KeywordHit] = Field(default_factory=list)
#     sales: SalesMetrics | None = None
#     sales_rank: int | None = None

# # 상품 후보 비교 API의 응답 데이터 스키마
# # 사용 시점: compare_product_candidates 실행 후 상품별 비교 결과를 반환할 때 사용한다.
# # 상품 속성의 차이, 키워드 매칭 결과, 판매 성과를 종합하여 반환한다.
# class ProductCompareData(BaseModel):
#     snapshot_id: int
#     rows: list[CompareRow]
#     differing_attributes: list[str] = Field(description="후보끼리 값이 다른 속성 (예: color)")