from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update, delete, or_
from sqlalchemy.orm import Session

from database.operations.orm.knowledge.knowledge_doc import (
    KnowledgeDoc
)

def replace_document_chunks(
        session:Session,
        source_doc_id: str,
        documents: list[dict]
):
    """동일 원본 문서의 기존 청크 비활성화 및 새 버전 청크 저장"""

    # 1. 검증

    # 1) 빈 청크 검증 (덮어쓰기 방지)
    if not documents:
        raise ValueError(f"저장할 청크가 없습니다: {source_doc_id}")

    # 2) 모든 청크의 원본 문서 ID 검증
    if any(doc["source_doc_id"] != source_doc_id for doc in documents):
        raise ValueError("원본 문서 ID가 일치하지 않습니다.")

    # 3) 모든 청크의 버전 검증
    versions = {doc["version"] for doc in documents}  # version 조회

    if len(versions) != 1:
        raise ValueError("모든 청크의 버전이 동일해야 합니다.")

    version = next(iter(versions)) # set의 요소 하나 반환

    # 2. 동일 원본 문서 + 동일 버전의 기존 청크 삭제 (버전이 다른 경우 pass)
    session.execute(
        delete(KnowledgeDoc)
        .where(
            KnowledgeDoc.source_doc_id == source_doc_id,
            KnowledgeDoc.version == version
        )
    )

    # 3. 동일 원본 문서의 기존 버전 행 모두 비활성화
    session.execute(
        update(KnowledgeDoc)
        .where(KnowledgeDoc.source_doc_id == source_doc_id)
        .values(status="inactive")
    )

    # 4. 새로운 버전 저장
    for document in documents:
        session.add(
            KnowledgeDoc(
                **document
            )
        )

def search_knowledge(
        session: Session,
        query_embedding: list[float],
        knowledge_type: str | None = None,
        category: str | None = None,
        top_k: int = 5
):
    """벡터 유사도 기반 업무 지식 검색"""

    today = datetime.now(ZoneInfo("Asia/Seoul")).date() # 한국 날짜 기준

    # 코사인 거리 계산
    distance = KnowledgeDoc.embedding.cosine_distance(
        query_embedding
    )

    # 쿼리 작성 (리트리버 기능 수행)
    statement = (
        select(
            KnowledgeDoc,
            distance.label("distance")
        )
        .where(
            KnowledgeDoc.status == "active",
            or_(
                KnowledgeDoc.effective_from.is_(None),
                KnowledgeDoc.effective_from <= today
            ),
            or_(
                KnowledgeDoc.effective_to.is_(None),
                KnowledgeDoc.effective_to >= today
            )
        )
    )

    if knowledge_type:
        statement = statement.where(
            KnowledgeDoc.knowledge_type == knowledge_type
        )

    if category:
        statement = statement.where(
            KnowledgeDoc.category == category
        )

    statement = (
        statement
        .order_by(distance)
        .limit(top_k)
    )

    results = session.execute(statement).all()

    return [
        {
            "doc_id": document.doc_id,
            "source_doc_id": document.source_doc_id,
            "title": document.title,
            "knowledge_type": document.knowledge_type,
            "category": document.category,
            "chunk_index": document.chunk_index,
            "body": document.body,
            "metadata": document. metadata_json,
            "version": document.version,
            "similarity": round(1 - distance_value, 4) # 유사도 점수 변환
        } 
        for document, distance_value in results
    ]
