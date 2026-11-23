from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

# 문서의 큰 주제
KnowledgeType = Literal[
    "business_guide",
    "product_guide",
    "content_guide",
    "product_placement",
    "styling"
]

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