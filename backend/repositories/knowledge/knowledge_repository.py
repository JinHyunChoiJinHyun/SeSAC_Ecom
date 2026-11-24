from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update, delete, or_
from sqlalchemy.orm import Session

from database.operations.orm.knowledge.knowledge_doc import (
    KnowledgeDoc
)

def is_document_changed(existing_chunks, new_documents):
    """기존 문서와 새 문서의 변경 여부 확인
    - existing_chunks는 비어 있지 않고 chunk_index 순으로 정렬 필수
    """

    existing = existing_chunks[0]
    new = new_documents[0]

    compare_fields = [
        "title",
        "knowledge_type",
        "category",
        "metadata_json",
        "effective_from",
        "effective_to",
    ]

    # 문서 공통 속성 비교
    for field in compare_fields:
        if getattr(existing, field) != new[field]:
            return True

    # 청크 본문 비교
    existing_bodies = [chunk.body for chunk in existing_chunks]
    new_bodies = [doc["body"] for doc in new_documents]

    return existing_bodies != new_bodies

def increase_version(version: str) -> str:
    """마이너 버전 증가 (형식: major.minor)"""
    
    try:
        major, minor = map(int, version.split("."))
    except ValueError as error:
        raise ValueError(f"버전 형식이 올바르지 않습니다.: {version}") from error


    return f"{major}.{minor + 1}"


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

    # 2. 기존 활성 chunk 조회
    existing_chunks = (
        session.query(KnowledgeDoc)
        .filter(
            KnowledgeDoc.source_doc_id == source_doc_id,
            KnowledgeDoc.status == "active"
        )
        .order_by(KnowledgeDoc.chunk_index)
        .all()
    )

    # 3. 버전 결정
    # 1) 기존 문서가 존재하고 변경이 없는 경우 기존 버전 유지
    if existing_chunks and not is_document_changed(existing_chunks, documents):
        return

    # 2) 비활성 이력까지 포함한 전체 버전 조회
    versions = session.execute(
        select(KnowledgeDoc.version)
        .where(KnowledgeDoc.source_doc_id == source_doc_id)
        .distinct()
    ).scalars().all() # 값만 반환

    # 3-1) 기존 버전이 존재하면 최댓값에서 마이너 버전 증가 
    if versions:
        # versions를 숫자로 변환하여 최댓값 반환
        latest_version = max(versions, key=lambda value: tuple(map(int, value.split(".")))) 
        version = increase_version(latest_version)

    # 3-2) 최초 적재 시
    else:
        version = "1.0"

    # 4. 모든 청크에 결정된 버전 적용
    for index, document in enumerate(documents):
        document["version"] = version
        document["doc_id"] = f"{source_doc_id}:{version}:{index}"

    # 5. 기존 버전 비활성화
    session.execute(
        update(KnowledgeDoc)
        .where(KnowledgeDoc.source_doc_id == source_doc_id)
        .values(status="inactive")
    )

    # 6. 동일 원본 문서 + 동일 버전의 기존 청크 삭제 (버전이 다른 경우 pass)
    session.execute(
        delete(KnowledgeDoc)
        .where(
            KnowledgeDoc.source_doc_id == source_doc_id,
            KnowledgeDoc.version == version
        )
    )

    # 7. 새로운 버전 저장
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
