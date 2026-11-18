"""보고서 문서 구조 → PDF(reportlab) · Word(python-docx).

필요 패키지: pip install reportlab python-docx  (없으면 그 형식만 실패로 알리고 다른 형식은 만듭니다)
PDF 한글 글꼴: 아래 순서로 찾아 문서에 넣습니다 (TrueType만 가능).
    1. 환경변수 CART_SIGNAL_REPORT_FONT (예: /Library/Fonts/NanumGothic.ttf)
    2. macOS AppleGothic · 나눔고딕 / Windows 맑은 고딕 / Linux 나눔고딕
    3. 없으면 reportlab 내장 한글 글꼴(HYGothic-Medium, 문서에 글꼴을 넣지 않음 — PDF 보기 프로그램이 대체 글꼴로 표시)
"""

from __future__ import annotations

import html
import os
from pathlib import Path

from agent_system.common_tools.report_export.document import (Bullets, Code, Image, ImageGrid, Paragraph,
                                                              ReportDocument, Table)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    str(Path.home() / "Library/Fonts/NanumGothic.ttf"),
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]
_font_name = None


def _pdf_font() -> str:
    global _font_name
    if _font_name:
        return _font_name
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for path in [os.getenv("CART_SIGNAL_REPORT_FONT", "")] + FONT_CANDIDATES:
        if path and Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont("ReportKorean", path))
                _font_name = "ReportKorean"
                return _font_name
            except Exception:
                continue
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
    _font_name = "HYGothic-Medium"
    return _font_name


def _esc(value) -> str:
    return html.escape(str(value if value is not None else "")).replace("\n", "<br/>")


def _grid_rows(block: ImageGrid) -> list[list[tuple[str, str]]]:
    cols = max(1, int(block.columns or 3))
    items = list(block.items)
    return [items[i:i + cols] for i in range(0, len(items), cols)]


def _pdf_image_grid(block, width, st, RLImage, RLTable, TableStyle, P, KeepTogether, mm):
    """상품 이미지 여러 장 → 표 한 개 (칸마다 이미지 + 설명). 없는 파일은 '(이미지 파일 없음)'."""
    cols = max(1, int(block.columns or 3))
    cell_w = width / cols
    max_w, max_h = cell_w - 6 * mm, 62 * mm
    data = []
    for row in _grid_rows(block):
        cells = []
        for path, label in row:
            if Path(path).is_file():
                image = RLImage(path)
                scale = min(max_w / image.imageWidth, max_h / image.imageHeight)
                image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
                cells.append([image, P(_esc(label), st["cell"])])
            else:
                cells.append([P(_esc(f"(이미지 파일 없음) {label}"), st["cell"])])
        cells += [""] * (cols - len(cells))
        data.append(cells)
    table = RLTable(data, colWidths=[cell_w] * cols)
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    parts = [table]
    if block.caption:
        parts.append(P(_esc(block.caption), st["muted"]))
    return KeepTogether(parts)


# ---------------------------------------------------------------- PDF
def write_pdf(doc: ReportDocument, path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import KeepTogether, ListFlowable, ListItem
    from reportlab.platypus import Paragraph as P
    from reportlab.platypus import Preformatted, SimpleDocTemplate, Spacer
    from reportlab.platypus import Table as RLTable
    from reportlab.platypus import TableStyle

    font = _pdf_font()
    width = A4[0] - 30 * mm
    st = {
        "title": ParagraphStyle("t", fontName=font, fontSize=19, leading=25, spaceAfter=4),
        "sub": ParagraphStyle("s", fontName=font, fontSize=10.5, leading=15, textColor=colors.HexColor("#5b6476")),
        "h1": ParagraphStyle("h1", fontName=font, fontSize=13.5, leading=19, spaceBefore=12, spaceAfter=5,
                             textColor=colors.HexColor("#1d2433")),
        "h2": ParagraphStyle("h2", fontName=font, fontSize=11.5, leading=16, spaceBefore=9, spaceAfter=4),
        "body": ParagraphStyle("b", fontName=font, fontSize=9.4, leading=14.5, spaceAfter=4, wordWrap="CJK"),
        "muted": ParagraphStyle("m", fontName=font, fontSize=8.6, leading=12.5, textColor=colors.HexColor("#5b6476"),
                                wordWrap="CJK"),
        "notice": ParagraphStyle("n", fontName=font, fontSize=9, leading=13.5, backColor=colors.HexColor("#fff6e5"),
                                 borderPadding=5, spaceAfter=6, wordWrap="CJK"),
        "cell": ParagraphStyle("c", fontName=font, fontSize=7.6, leading=10, wordWrap="CJK"),
        "code": ParagraphStyle("code", fontName="Courier", fontSize=7.2, leading=9.2,
                               backColor=colors.HexColor("#f4f6fa"), borderPadding=4),
    }
    story = [P(_esc(doc.title), st["title"])]
    if doc.subtitle:
        story.append(P(_esc(doc.subtitle), st["sub"]))
    if doc.meta:
        meta = RLTable([[P(_esc(k), st["cell"]), P(_esc(v), st["cell"])] for k, v in doc.meta],
                       colWidths=[32 * mm, width - 32 * mm])
        meta.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f6fa")),
                                  ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dee7")),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [Spacer(1, 6), meta]

    for section in doc.sections:
        story.append(P(_esc(section.heading), st["h1" if section.level == 1 else "h2"]))
        for block in section.blocks:
            if isinstance(block, Paragraph):
                story.append(P(_esc(block.text), st.get(block.style, st["body"])))
            elif isinstance(block, Bullets):
                story.append(ListFlowable([ListItem(P(_esc(i), st["body"]), leftIndent=10) for i in block.items],
                                          bulletType="bullet", start="•", leftIndent=10))
            elif isinstance(block, Table):
                data = [[P(_esc(c), st["cell"]) for c in block.columns]]
                data += [[P(_esc(v), st["cell"]) for v in row] for row in block.rows]
                table = RLTable(data, repeatRows=1, colWidths=[width / max(1, len(block.columns))] * len(block.columns))
                table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f6")),
                                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dee7")),
                                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                story += [Spacer(1, 3), table]
                if block.note:
                    story.append(P(_esc(block.note), st["muted"]))
                story.append(Spacer(1, 4))
            elif isinstance(block, Image):
                if Path(block.path).is_file():
                    image = RLImage(block.path)
                    scale = min(1.0, (width * 0.9) / image.imageWidth, (110 * mm) / image.imageHeight)
                    image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
                    story.append(KeepTogether([image, P(_esc(block.caption), st["muted"])]))
                else:
                    story.append(P(_esc(f"(그래프 파일 없음: {Path(block.path).name})"), st["muted"]))
            elif isinstance(block, ImageGrid):
                if not block.items:
                    continue
                story.append(_pdf_image_grid(block, width, st, RLImage, RLTable, TableStyle, P, KeepTogether, mm))
            elif isinstance(block, Code):
                if block.title:
                    story.append(P(_esc(block.title), st["muted"]))
                story.append(Preformatted(block.text, st["code"], maxLineLength=110))
    if doc.footer:
        story += [Spacer(1, 10), P(_esc(doc.footer), st["muted"])]

    def page_number(canvas, _doc):
        canvas.setFont(font, 7.5)
        canvas.setFillColor(colors.HexColor("#8a93a3"))
        canvas.drawCentredString(A4[0] / 2, 9 * mm, f"{doc.title} · {canvas.getPageNumber()}")

    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                      bottomMargin=16 * mm, title=doc.title).build(story, onFirstPage=page_number,
                                                                    onLaterPages=page_number)
    return path


# ---------------------------------------------------------------- Word
def write_docx(doc: ReportDocument, path: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    word = Document()
    normal = word.styles["Normal"]
    normal.font.name = "Malgun Gothic"
    normal.font.size = Pt(10)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
    for section in word.sections:
        section.left_margin = section.right_margin = Mm(18)

    def muted(paragraph):
        for run in paragraph.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x5B, 0x64, 0x76)

    word.add_heading(doc.title, level=0)
    if doc.subtitle:
        muted(word.add_paragraph(doc.subtitle))
    if doc.meta:
        meta = word.add_table(rows=0, cols=2)
        meta.style = "Table Grid"
        for key, value in doc.meta:
            cells = meta.add_row().cells
            cells[0].text, cells[1].text = str(key), str(value)

    for section in doc.sections:
        word.add_heading(section.heading, level=1 if section.level == 1 else 2)
        for block in section.blocks:
            if isinstance(block, Paragraph):
                paragraph = word.add_paragraph(block.text)
                if block.style in ("muted", "notice"):
                    muted(paragraph)
            elif isinstance(block, Bullets):
                for item in block.items:
                    word.add_paragraph(item, style="List Bullet")
            elif isinstance(block, Table):
                table = word.add_table(rows=1, cols=len(block.columns))
                table.style = "Table Grid"
                for cell, name in zip(table.rows[0].cells, block.columns):
                    cell.text = str(name)
                    for run in cell.paragraphs[0].runs:
                        run.font.bold = True
                for row in block.rows:
                    cells = table.add_row().cells
                    for cell, value in zip(cells, row):
                        cell.text = "" if value is None else str(value)
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            for run in paragraph.runs:
                                run.font.size = Pt(8)
                if block.note:
                    muted(word.add_paragraph(block.note))
            elif isinstance(block, Image):
                if Path(block.path).is_file():
                    word.add_picture(block.path, width=Mm(160))
                    caption = word.add_paragraph(block.caption)
                    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    muted(caption)
                else:
                    muted(word.add_paragraph(f"(그래프 파일 없음: {Path(block.path).name})"))
            elif isinstance(block, ImageGrid):
                if not block.items:
                    continue
                cols = max(1, int(block.columns or 3))
                grid = word.add_table(rows=0, cols=cols)
                for row in _grid_rows(block):
                    cells = grid.add_row().cells
                    for cell, (image_path, label) in zip(cells, row):
                        paragraph = cell.paragraphs[0]
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        if Path(image_path).is_file():
                            paragraph.add_run().add_picture(image_path, height=Mm(55))
                            muted(cell.add_paragraph(label))
                        else:
                            muted(cell.add_paragraph(f"(이미지 파일 없음) {label}"))
                if block.caption:
                    muted(word.add_paragraph(block.caption))
            elif isinstance(block, Code):
                if block.title:
                    muted(word.add_paragraph(block.title))
                code = word.add_paragraph()
                run = code.add_run(block.text)
                run.font.name = "Consolas"
                run.font.size = Pt(8)
    if doc.footer:
        muted(word.add_paragraph(doc.footer))
    path.parent.mkdir(parents=True, exist_ok=True)
    word.save(str(path))
    return path


def export_report(doc: ReportDocument, out_dir: Path, base_name: str) -> dict:
    """PDF와 Word 2부 만들기. 반환: {"files": [경로…], "errors": {"pdf"|"docx": 이유}}"""
    files, errors = [], {}
    for ext, writer, package in (("pdf", write_pdf, "reportlab"), ("docx", write_docx, "python-docx")):
        try:
            files.append(str(writer(doc, Path(out_dir) / f"{base_name}.{ext}")))
        except ImportError:
            errors[ext] = f"{package} 패키지가 없습니다 (pip install {package})"
        except Exception as exc:  # 한 형식이 실패해도 다른 형식은 전달
            errors[ext] = f"{type(exc).__name__}: {exc}"
    return {"files": files, "errors": errors}
