from sqlalchemy.orm import Session

from rag.embedding import embed_query

from backend.repositories.knowledge.knowledge_repository import (
    search_knowledge
)

def search_business_knowledge(
        session: Session,
        query: str,
        knowledge_type: str | None = None,
        category: str | None = None,
        top_k: int = 5
):
    """업무 지식 검색"""

    # 빈 질문 검색 방지
    if not query or not query.strip():
        raise ValueError("검색어가 비어 있습니다.")

    # top_k 범위 제한
    if not 1 < top_k <= 10:
        raise ValueError("top_k는 1~10 사이여야 합니다.")

    query_embedding = embed_query(query)

    results = search_knowledge(
        session=session,
        query_embedding=query_embedding,
        knowledge_type=knowledge_type,
        category=category, # 문서의 세부 카테고리
        top_k=top_k
    )

    return {
        "query": query,
        "results": results,
        "result_count": len(results)
    }