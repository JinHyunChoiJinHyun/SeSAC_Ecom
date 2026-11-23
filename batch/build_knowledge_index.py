import json

from collections import Counter
from datetime import date
from pathlib import Path

from rag.chunk import split_text
from rag.embedding import embeded_documents

from database.operations.connection import get_session_factory

from backend.repositories.knowledge.knowledge_repository import (
    replace_document_chunks
)

DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "knowledge"
    / "business_knowledge.jsonl"
)

def load_documents():
    """JSONL 문서 불러오기"""
    documents = []

    with open(DATA_PATH, "r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                documents.append(json.loads(line))

    return documents

def index_knowledge_documents():
    """"""

    documents = load_documents()

    # 1. 원본 문서 ID 중복 검사
    doc_ids = [document["doc_id"] for document in documents]

    duplicates = [
        doc_id
        for doc_id, count in Counter(doc_ids).items()
        if count > 1
    ]

    if duplicates:
        raise ValueError(
            f"JSONL에 중복된 doc_id가 있습니다: {duplicates}"
        )

    # 2. 문서별 청킹 및 임베딩
    with get_session_factory()() as session:
        try:
            for document in documents:
                chunks = split_text(document["content"])

                if not chunks:
                    raise ValueError(
                        f"문서의 청킹 결과가 비어 있습니다: {document["doc_id"]}"
                    )

                embeddings = embeded_documents(chunks)

                if len(chunks) != len(embeddings):
                    raise ValueError(
                        f"청크와 임베딩 개수가 일치하지 않습니다: "
                        f"{document["doc_id"]}"
                    )

                chunk_documents = []

                for index, (chunk, embedding) in enumerate(
                    zip(chunks, embeddings)
                ):
                    chunk_documents.append(
                        {
                            "doc_id": (
                                f"{document["doc_id"]}:"
                                f"{document["version"]}:"
                                f"{index}:"
                            ),
                            "source_doc_id": document["doc_id"], # 원본 문서 ID
                            "title":document["title"],
                            "knowledge_type": document["knowledge_type"],
                            "category": document["category"],
                            "chunk_index": index,
                            "body": chunk,
                            "metadata_json": document.get("metadata", {}),
                            "version": document["version"],
                            "status": "active",
                            "effective_from": (
                                date.fromisoformat(document["effective_from"])
                                if document.get("effective_from")
                                else None
                            ),
                            "effective_to": (
                                date.fromisoformat(document["effective_to"])
                                if document.get("effective_to")
                                else None
                            ),
                            "embedding": embedding
                        }
                    )
                replace_document_chunks(
                    session=session,
                    source_doc_id=document["doc_id"],
                    documents=chunk_documents
                )
            session.commit()
        except Exception:
            session.rollback()
            raise

if __name__ == "__main__":
    index_knowledge_documents()