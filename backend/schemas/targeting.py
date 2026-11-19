from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    field_validator,
    model_validator
)

RULE_BASED_MODEL_PREFIX = "rule_" # ML 모델이 없거나 로딩에 실패했을 때 사용할 규칙 기반 모델의 버전 접두어
MAX_PRODUCTS_PER_REQUEST = 50 # 한번의 요청에 계산할 최대 상품 수

# ---------------------------------------------------------------- 요청

class TargetScoresRequest(BaseModel):
    """구매 가능성 점수 계산 요청"""
    model_config = ConfigDict(extra="forbid") # 정의되지 않은 필드 입력 시 오류 발생

    campaign_id: UUID = Field(..., description="점수를 계산할 캠페인(기획전) ID")
    campaign_product_ids: Annotated[
        list[UUID],
        Field(min_length=1, max_length=MAX_PRODUCTS_PER_REQUEST)
    ] | None = Field(
        None,
        description="계산할 캠페인 상품 ID. 생략(null)하면 기획전 확정된 상품 전체. 빈 목록은 허용하지 않음",
    )

    @field_validator("campaign_product_ids")
    @classmethod
    def _validate_no_duplicates(cls, values: list[UUID] | None) -> list[UUID] | None:
        if values is None:
            return values

        seen: set[UUID] = set()
        duplicates: set[UUID] = set()

        for product_id in values:
            if product_id in seen:
                duplicates.add(product_id)
            else:
                seen.add(product_id)

        if duplicates:
            raise ValueError(
                f"중복된 campaign_product_id: {sorted(map(str, duplicates))}"
            )

        return values

# ---------------------------------------------------------------- 응답

class ProductScoreSummary(BaseModel):
    campaign_produt_id: UUID
    total_candidate_count: NonNegativeInt # 계산 대상 후보자 수
    customer_counts_by_segment: dict[
        Annotated[str, Field(min_length=1)], NonNegativeInt
    ] = Field(..., description="4분면별 인원 (키는 scoring.yaml의 세그먼트명)")

    @model_validator(mode="after")
    def _segments_sum_to_candidates(self):
        """세그먼트별 인원수 합계와 전체 후보 고객 수의 정합성 검증"""
        total = sum(self.customer_counts_by_segment.values())
        if total != self.total_candidate_count:
            raise ValueError(
                f"customer_counts_by_segment 합계({total})가 total_candidate_count({self.total_candidate_count})와 다름"
            )
        return self

class TargetScoreResponse(BaseModel):
    """구매 가능성 점수 계산 결과 (scoring_run 요약)"""

    scoring_run_id: UUID # FK 개념

    # 과거와 동일한 조건으로 다시 계산하고 결과를 비교하기 위한 정보 (재현성)
    model_name: str
    model_version: str
    feature_set_version: str | None
    scoring_method: str
    scoring_config_version: str
    feature_as_of: date

    # 대체 경로
    used_fallback: bool
    fallback_reason: Literal["no_active_model", "load_failed"] | None = None
    




        

