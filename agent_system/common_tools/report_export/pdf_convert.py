"""Word(DOCX) → PDF 변환 (LibreOffice, 한글 글꼴 확인).

⑥ Reporting Agent의 export_report_pdf Tool이 로컬 모드에서 이 함수를 씁니다
(agent_system/tools/reporting_tools.py). Slack 업로드 처리(interfaces/slack/report_finalize.py)도 Reporting Agent를 거칩니다.

- 수정본 DOCX 자체를 변환하고, 분석 데이터로 보고서를 다시 만들지 않습니다.
- 이 컴퓨터에 있는 한글 글꼴을 적용하고, PDF에 한글 글꼴이 실제로 들어갔는지 확인합니다.
- LibreOffice가 시스템 글꼴 폴더를 보도록 글꼴 설정(fontconfig)을 직접 지정합니다.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _office_executable() -> str:
    custom = os.getenv("CART_SIGNAL_OFFICE_BIN", "").strip()
    paths = [custom] if custom else []
    paths += ["/Applications/LibreOffice.app/Contents/MacOS/soffice", "soffice", "libreoffice"]
    for item in paths:
        if item and (Path(item).is_file() or shutil.which(item)):
            return item
    raise RuntimeError("LibreOffice가 필요합니다. macOS: brew install --cask libreoffice")



# CART_SIGNAL_KOREAN_FONT_FIX_V2
# 한글 PDF 글꼴: 이 컴퓨터에 실제로 있는 글꼴만 씁니다.
# (V1은 "Noto Sans CJK KR"로 고정 → 맥에 없으면 LibreOffice가 한글 없는 글꼴(Linux Libertine)로 바꿔 한글이 사라짐)
#   (글꼴 이름, 글꼴 파일 후보, PDF 안 글꼴 이름에 들어가는 표시)
KOREAN_FONTS = [
    ("Apple SD Gothic Neo", ["/System/Library/Fonts/AppleSDGothicNeo.ttc"], "AppleSDGothicNeo"),
    ("AppleGothic", ["/System/Library/Fonts/Supplemental/AppleGothic.ttf", "/Library/Fonts/AppleGothic.ttf"],
     "AppleGothic"),
    ("NanumGothic", ["~/Library/Fonts/NanumGothic.ttf", "/Library/Fonts/NanumGothic.ttf",
                     "/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "C:/Windows/Fonts/NanumGothic.ttf"],
     "NanumGothic"),
    ("Malgun Gothic", ["C:/Windows/Fonts/malgun.ttf"], "MalgunGothic"),
    ("Noto Sans CJK KR", ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                          "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                          "~/Library/Fonts/NotoSansCJKkr-Regular.otf", "/Library/Fonts/NotoSansCJKkr-Regular.otf"],
     "NotoSansCJK"),
]
# PDF에 이 표시가 들어간 글꼴이 있으면 한글이 들어간 것으로 봄 (요청한 글꼴 대신 다른 한글 글꼴로 바뀐 경우 포함)
KOREAN_FONT_MARKERS = ("AppleSDGothicNeo", "AppleGothic", "AppleMyungjo", "Nanum", "Malgun", "NotoSansCJK",
                       "NotoSerifCJK", "NotoSansKR", "Gulim", "Dotum", "Batang")
_HANGUL = re.compile(r"[\uac00-\ud7a3]")


def korean_font_candidates() -> list[tuple[str, str]]:
    """쓸 수 있는 한글 글꼴 [(이름, PDF 표시)] — 환경변수 CART_SIGNAL_PDF_FONT가 있으면 그 글꼴을 맨 앞에."""
    found = []
    custom = os.getenv("CART_SIGNAL_PDF_FONT", "").strip()
    if custom:
        found.append((custom, re.sub(r"\s+", "", custom)))
    for name, files, marker in KOREAN_FONTS:
        if any(Path(f).expanduser().is_file() for f in files) and name not in [n for n, _ in found]:
            found.append((name, marker))
    return found


def pdf_font_names(pdf: Path) -> list[str]:
    """PDF에 들어간 글꼴 이름 (예: BAAAAA+AppleGothic → AppleGothic)."""
    names = re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_.]+)", pdf.read_bytes())
    return sorted({n.decode("ascii", "ignore").split("+", 1)[-1] for n in names})


def has_korean_font(fonts: list[str], extra: str = "") -> bool:
    markers = KOREAN_FONT_MARKERS + ((extra,) if extra else ())
    return any(m.lower() in f.lower() for f in fonts for m in markers)


def docx_has_korean(path: Path) -> bool:
    with zipfile.ZipFile(path) as z:
        return bool(_HANGUL.search(z.read("word/document.xml").decode("utf-8", "ignore")))


def _prepare_korean_docx(source: Path, destination: Path, font_name: str) -> None:
    """PDF 변환용 임시 Word에 한글 지원 글꼴을 적용합니다 (사용자 원본 파일은 바꾸지 않음)."""
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(source)

    def apply_font(font):
        font.name = font_name

    def set_east_asia(element):
        rpr = element.get_or_add_rPr()
        fonts = rpr.rFonts

        if fonts is None:
            from docx.oxml import OxmlElement
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)

        fonts.set(qn("w:ascii"), font_name)
        fonts.set(qn("w:hAnsi"), font_name)
        fonts.set(qn("w:eastAsia"), font_name)
        fonts.set(qn("w:cs"), font_name)

        for key in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            attr = qn("w:" + key)
            if attr in fonts.attrib:
                del fonts.attrib[attr]

    # 문서 스타일의 글꼴 지정
    for style in doc.styles:
        try:
            apply_font(style.font)
            set_east_asia(style._element)
        except (AttributeError, TypeError, ValueError):
            continue

    def process_paragraph(paragraph):
        for run in paragraph.runs:
            apply_font(run.font)
            set_east_asia(run._element)

    def process_container(container):
        for paragraph in container.paragraphs:
            process_paragraph(paragraph)

        for table in container.tables:
            for row in table.rows:
                for cell in row.cells:
                    process_container(cell)

    # 본문과 표
    process_container(doc)

    # 머리글과 바닥글
    for section in doc.sections:
        process_container(section.header)
        process_container(section.footer)

    doc.save(destination)
    print(f"[PDF 글꼴] {font_name} 적용", flush=True)


# LibreOffice가 시스템 글꼴을 보도록 하는 글꼴 설정 (CART_SIGNAL_KOREAN_FONT_FIX_V3)
# 11/20 맥 로그: LibreOffice가 fontconfig로 글꼴을 찾는데 설정에 글꼴 폴더가 없어(Fontconfig warning)
# 자체 내장 글꼴(Linux Libertine 등, 한글 없음)만 보였음 → 맥·리눅스·윈도우 글꼴 폴더를 직접 알려 줌
FONT_DIRS = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts", "~/Library/Fonts",
             "/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts", "C:/Windows/Fonts"]
FONT_CACHE_DIR = Path.home() / ".cache" / "cart_signal_fontconfig"     # 첫 변환 때 한 번 만들고 다음부터 재사용
OFFICE_TIMEOUT_SEC = 180                                                # 첫 변환은 글꼴 목록을 만드느라 오래 걸릴 수 있음


def _office_env(profile: str) -> dict:
    """LibreOffice 실행 환경: 글꼴 폴더를 적은 fontconfig 설정 파일을 지정."""
    from xml.sax.saxutils import escape

    dirs = [str(Path(d).expanduser()) for d in FONT_DIRS if Path(d).expanduser().is_dir()]
    FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conf = Path(profile) / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
        + "".join(f"  <dir>{escape(d)}</dir>\n" for d in dirs)
        + f"  <cachedir>{escape(str(FONT_CACHE_DIR))}</cachedir>\n"
        + '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n</fontconfig>\n',
        encoding="utf-8")
    env = os.environ.copy()
    env.pop("FONTCONFIG_PATH", None)
    env["FONTCONFIG_FILE"] = str(conf)
    print(f"[PDF 글꼴] 글꼴 폴더 {len(dirs)}개 지정: {dirs}", flush=True)
    return env


def _run_office(executable: str, prepared: Path, out_dir: Path, profile: str) -> Path:
    command = [
        executable,
        f"-env:UserInstallation={Path(profile).as_uri()}",
        "--headless", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(out_dir), str(prepared),
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=OFFICE_TIMEOUT_SEC, check=False,
                          env=_office_env(profile))
    print(
        f"[PDF 변환] 종료 코드={proc.returncode}, "
        f"stdout={proc.stdout[:500]!r}, stderr={proc.stderr[:500]!r}",
        flush=True,
    )
    pdf = out_dir / (prepared.stem + ".pdf")
    if proc.returncode != 0 or not pdf.is_file() or pdf.read_bytes()[:4] != b"%PDF":
        raise RuntimeError("PDF 변환 실패: " + (proc.stderr or proc.stdout or "출력 파일 없음")[:250])
    return pdf


def convert_docx_to_pdf(docx: Path, out_dir: Path) -> Path:
    """수정본 DOCX 자체를 변환. 생성했던 분석 데이터로 보고서를 재작성하지 않습니다.

    한글이 있는 문서는 이 컴퓨터에 있는 한글 글꼴을 차례로 적용해 변환하고,
    PDF에 그 글꼴이 실제로 들어갔는지 확인합니다. 어느 글꼴로도 안 되면 한글이 빠진 PDF를 보내지 않고 오류로 알립니다.
    """
    print("[PDF 변환] LibreOffice 실행 준비", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    executable = _office_executable()
    korean = docx_has_korean(docx)
    candidates = korean_font_candidates() if korean else [("", "")]
    if korean and not candidates:
        raise RuntimeError("한글 글꼴을 찾지 못했습니다. 나눔고딕을 설치하거나 CART_SIGNAL_PDF_FONT에 글꼴 이름을 지정해 주세요.")
    tried = []
    for font_name, marker in candidates:
        with tempfile.TemporaryDirectory(prefix="cart_signal_lo_") as profile:
            prepared = Path(profile) / docx.name
            if font_name:
                _prepare_korean_docx(docx, prepared, font_name)
            else:
                shutil.copyfile(docx, prepared)
            pdf = _run_office(executable, prepared, out_dir, profile)
        fonts = pdf_font_names(pdf)
        print(f"[PDF 글꼴] 들어간 글꼴: {fonts}", flush=True)
        if not korean or has_korean_font(fonts, marker):
            final = out_dir / (docx.stem + ".pdf")
            if pdf != final:
                os.replace(pdf, final)
            return final
        tried.append(font_name)
        pdf.unlink(missing_ok=True)
        print(f"[PDF 글꼴] {font_name}로 변환한 PDF에 한글 글꼴이 없어 다음 글꼴로 다시 변환", flush=True)
    raise RuntimeError(f"한글 글꼴이 PDF에 들어가지 않았습니다 (시도: {', '.join(tried)}).")
