from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T") # 제네릭 타입 변수

SourceType = Literal[
    "hm_product", # H&M 상품 속성
    "hm_sales", # H&M 판매 분석
    "external_review", # 무신사 리뷰 분석
    "trend", # 네이버 검색 추이
    "youtube", # 유튜브 지표
    "fashion_media", # 셀럽 착장 / 패션 매체
    "past_campaign", # 과거 기획전 (RAG)
]

class Evidence(BaseModel):
    """결과의 근거 스키마"""
    source_type: SourceType
    source_id: str = Field(description="article_id 또는 snapshot:{id} 등")
    label: str | None = Field(None, description="사람이 읽는 짧은 설명")

    @property
    def ref(self) -> str:
        """content_evidence / 검증에서 사용할 한 줄 표기 예: hm_product:0108775015"""
        return f"{self.source_type}:{self.source_id}"

class ToolResult(BaseModel, Generic[T]):
    """모든 Tool 응답의 공통 스키마"""
    data: T
    evidence: list[Evidence] = Field(default_factory=list)

