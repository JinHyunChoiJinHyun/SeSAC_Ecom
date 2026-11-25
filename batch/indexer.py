from datetime import date

from rag.chunk import split_text
from rag.embedding import embeded_documents

from database.operations.connection import get_session_factory

from backend.repositories.knowledge.knowledge_repository import (
     replace_document_chunks
)

def index_documents(documents):
    """문서 청킹, 임베딩 및 DB 저장"""

    with get_session_factory()() as session:
        try:
            for document in documents:

                # 1. 청킹
                chunks = split_text(document.content)

                if not chunks:
                    raise ValueError(
                        f"문서의 청킹 결과가 비어 있습니다: {document.doc_id}"
                    )

                # 2. 임베딩
                embeddings = embeded_documents(chunks)

                if len(chunks) != len(embeddings):
                    raise ValueError(
                        f"청크와 임베딩 개수가 일치하지 않습니다: "
                        f"{document.doc_id}"
                    )

                # 3. DB 저장 데이터 생성
                chunk_documents = []

                for index, (chunk, embedding) in enumerate(
                    zip(chunks, embeddings)
                ):
                    chunk_documents.append(
                        {
                            "source_doc_id": document.doc_id, # 원본 문서 ID
                            "title":document.title,
                            "knowledge_type": document.knowledge_type,
                            "category": document.category,
                            "chunk_index": index,
                            "body": chunk,
                            "metadata_json": document.metadata,
                            "status": "active",
                            "effective_from": (
                                date.fromisoformat(document.effective_from)
                                if document.effective_from
                                else date.today()
                            ),
                            "effective_to": (
                                date.fromisoformat(document.effective_to)
                                if document.effective_to
                                else None
                            ),
                            "embedding": embedding
                        }
                    )

                # 4. DB 저장
                replace_document_chunks(
                    session=session,
                    source_doc_id=document.doc_id,
                    documents=chunk_documents
                )
            session.commit()
            
        except Exception:
            session.rollback()
            raise