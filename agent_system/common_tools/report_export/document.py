"""보고서 문서 구조 — 출력 형식(PDF·Word)과 무관한 중간 표현."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paragraph:
    text: str
    style: str = "body"          # body / muted / notice


@dataclass
class Bullets:
    items: list[str]


@dataclass
class Table:
    columns: list[str]
    rows: list[list]
    note: str = ""               # 예: "전체 120행 중 앞 20행"


@dataclass
class Image:
    path: str
    caption: str = ""


@dataclass
class ImageGrid:
    """여러 장의 작은 이미지 (예: 상품 후보 이미지). items = [(파일 경로, 설명), …]"""
    items: list[tuple[str, str]]
    caption: str = ""
    columns: int = 3


@dataclass
class Code:
    text: str
    title: str = ""


@dataclass
class Section:
    heading: str
    blocks: list = field(default_factory=list)
    level: int = 1               # 1 = 큰 제목, 2 = 작은 제목


@dataclass
class ReportDocument:
    kind: str                    # analysis / campaign
    title: str
    subtitle: str = ""
    meta: list[tuple[str, str]] = field(default_factory=list)   # 표지 정보 (항목, 값)
    sections: list[Section] = field(default_factory=list)
    footer: str = ""
