import logging
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

def extract_pdf_text(pdf_path: Path | str) -> str:
    """PDF 파일에서 전체 텍스트 추출"""

    pdf_path = Path(pdf_path)

    # 1. 파일 존재 여부 확인
    if not pdf_path.is_file():
        raise FileNotFoundError(
            f"PDF 파일을 찾을 수 없스비다: {pdf_path}"
        )

    # 2. PDF 열기 및 암호 확인
    with pymupdf.open(pdf_path) as pdf:
        if pdf.needs_pass:
            raise ValueError(
                f"암호화된 PDF 입니다: {pdf_path.name}"
            )

        page_texts = []
        empty_pages = []

        # 3. 페이지별 텍스트 추출
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text", sort=True).strip()

            if text:
                page_texts.append(
                    f"[페이지 {page_number}]\n{text}"
                )
            else:
                empty_pages.append(page_number)

    # 4. 전체 텍스트 추출 여부 확인
    if not page_texts:
        raise ValueError(
            f"추출된 텍스트가 없습니다: {pdf_path.name}"
        )

    # 5. 텍스트 없는 페이지 로깅
    if empty_pages:
        logger.warning(
            "%s: 텍스트 없는 페이지 %s",
            pdf_path.name,
            empty_pages
        )

    return "\n\n".join(page_texts)