from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Literal
from datetime import date

# 문서의 큰 주제
KnowledgeType = Literal[
    "business_guide",
    "product_guide",
    "content_guide",
]

class KnowledgeDocument(BaseModel):
    """입력 데이터 검증"""
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=255)
    knowledge_type: KnowledgeType
    category: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def check_effective_period(self):
        """적용 종료일이 시작일보다 빠르면 거부"""
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to는 effective_from보다 빠를 수 없습니다.")

        return self

class KnowledgeSearchRequest(BaseModel):
    """RAG 검색 요청"""

    # 문자열 앞뒤 공백 제거 후 검증
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=500)

    knowledge_type: KnowledgeType | None = None

    category: str | None = Field(
        default=None,
        max_length=100
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=10
    )

class KnowledgeSearchResult(BaseModel):
    """검색 결과 반환"""
    doc_id: str
    source_doc_id: str
    title: str
    knowledge_type: KnowledgeType
    category: str | None
    chunk_index: int
    body: str
    metadata: dict
    version: str
    similarity: float

class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeSearchResult]
    result_count: int