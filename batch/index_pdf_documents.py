import unicodedata
from pathlib import Path
import logging

from typing import get_args, cast

from rag.pdf_reader import extract_pdf_text

from backend.schemas.knowledge import (
    KnowledgeDocument,
    KnowledgeType
)

from batch.indexer import index_documents

logger = logging.getLogger(__name__)

PDF_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "raw"
    / "knowledge"
    / "pdf"
)

def get_document_classification(pdf_path: Path) -> tuple[KnowledgeType, str]:
    """PDF 경로를 기준으로 지식 유형과 카테고리 결정"""

    # 1. PDF_DIR 기준 상대 경로 추출 및 구분
    relative_path = pdf_path.relative_to(PDF_DIR)
    parts = relative_path.parts

    # 2. 지식 유형 폴더, 카테고리 폴더, PDF 파일명 확인
    if len(parts) < 3:
        raise ValueError(
            f"지식 유형 또는 카테고리 폴더가 누락되었습니다: {relative_path}"
        )

    # 3. 첫 번째 폴더명에서 지식 유형 추출 및 정규화
    knowledge_type = unicodedata.normalize("NFC", parts[0]).strip()

    # 4. 추출한 지식 유형이 스키마에 정의된 허용 유형인지 검증
    if knowledge_type not in get_args(KnowledgeType):
        raise ValueError(
            f"지원하지 않는 지식 유형입니다: {knowledge_type}"
        )

    # 5. 두 번째 폴더명에서 카테고리 추출 및 정규화
    category = unicodedata.normalize("NFC", parts[1]).strip()

    # 6. 카테고리명 검증
    if not category:
        raise ValueError(
            f"카테고리 명이 비어 있습니다: {relative_path}"
        )

    return cast(KnowledgeType, knowledge_type), category

def load_pdf_documents():
    """PDF 파일을 읽어 KnowledgeDocument로 변환"""

    # 1. 파일 경로 검증
    if not PDF_DIR.is_dir():
        raise FileNotFoundError(f"PDF 폴더가 없습니다: {PDF_DIR}")

    documents = []
    failures = []

    # 2. PDF 파일 탐색 및 검증
    pdf_paths = sorted(
        path for path in PDF_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )

    for pdf_path in pdf_paths:
        relative_path = pdf_path.relative_to(PDF_DIR)

        try:
            # 1) 지식 우형 추출
            knowledge_type, category = get_document_classification(pdf_path)

            # 2) PDF 텍스트 추출
            content = extract_pdf_text(pdf_path)

            # 3) 문서 객체 생성
            document = KnowledgeDocument(
                doc_id=f"pdf:{relative_path.with_suffix("").as_posix()}",
                title=pdf_path.stem,
                knowledge_type=knowledge_type,
                category=category,
                content=content,
                metadata={
                    "source_type": "pdf",
                    "source_file": relative_path.as_posix()
                },
                effective_from=None,
                effective_to=None
            )

            documents.append(document)

        except Exception as error:
            failures.append(
                f"{relative_path}: {type(error).__name__} - {error}"
            )
            continue

    return documents, failures

def index_pdf_douments():
    """PDF 문서 임베딩 후 DB에 저장"""

    documents, failures = load_pdf_documents()

    # 문서 임베딩
    if documents:
        try:
            index_documents(documents)

            logger.info(
                "PDF 문서 인덱싱 완료 | 성공: %d건",
                len(documents)
            )

        except Exception:
            logger.exception(
                "PDF 문서 인덱싱 실패 | 대상 문서: %d건",
                len(documents)
            )
            return 1

    # 3. 개별 PDF 처리 실패 기록
    for failure in failures:
        logger.warning("PDF 처리 실패 | 파일: %s", failure)

    # 4. 전체 처리 결과 기록
    logger.info(
        "PDF 적재 작업 완료 | 문서 생성: %d건, 실패: %d건",
        len(documents),
        len(failures)
    )

    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(index_pdf_douments())
    