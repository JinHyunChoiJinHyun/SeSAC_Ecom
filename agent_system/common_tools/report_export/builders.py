"""기록 → 보고서 문서 구조.

    build_analysis_report(entries)                 일반 분석 보고서 (대화 중 여러 분석)
    build_campaign_report(campaign, run, entries)  기획전 실행 계획 보고서 (확정값 + 워크플로 단계 결과)

둘 다 기록된 실제 결과만 옮깁니다 (새 문장·숫자를 만들지 않음).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from agent_system.common_tools.report_export import analysis_log
from agent_system.common_tools.report_export.document import (Bullets, Code, Image, ImageGrid, Paragraph,
                                                              ReportDocument, Section, Table)

KST = timezone(timedelta(hours=9))
TABLE_ROWS = 20            # 보고서에 보여 줄 표 행 수
TABLE_COLUMNS = 8          # 보고서에 보여 줄 표 열 수
SOURCE_LABELS = {"hm": "H&M", "amazon": "Amazon", "musinsa": "무신사", "naver": "네이버 쇼핑인사이트",
                 "youtube": "유튜브", "fashion_media": "패션 매체", "targeting": "타깃 점수(백엔드)",
                 "content": "콘텐츠(백엔드)", "validation": "검증(백엔드)", "report": "보고서(백엔드)",
                 "execution": "캠페인 실행(백엔드)"}
STATUS_LABELS = {"done": "완료", "skipped": "건너뜀 (담당 Agent 연결 대기)", "failed": "실패"}
FOOTER = "CART SIGNAL이 대화 중 실제 분석 결과를 모아 만든 문서입니다. 새로 계산하거나 추정한 숫자는 없습니다."


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def clean_answer(text: str) -> str:
    """Slack·Markdown 표시 기호 정리 (**굵게**, # 제목)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", str(text or ""))
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    return text.strip()


def _answer_blocks(text: str) -> list:
    blocks, bullets, paragraph = [], [], []

    def flush():
        if paragraph:
            blocks.append(Paragraph(" ".join(paragraph)))
            paragraph.clear()
        if bullets:
            blocks.append(Bullets(list(bullets)))
            bullets.clear()

    for line in clean_answer(text).splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            flush()
            continue
        match = re.match(r"^(?:[-•*·]|\d+[.)])\s+(.*)$", stripped)
        if match:
            if paragraph:
                blocks.append(Paragraph(" ".join(paragraph)))
                paragraph.clear()
            bullets.append(match.group(1))
        else:
            if bullets:
                blocks.append(Bullets(list(bullets)))
                bullets.clear()
            paragraph.append(stripped)
    flush()
    return blocks


def _table_block(entry: dict, extra: dict | None = None) -> Table | None:
    df = analysis_log.load_table(entry, extra.get("path") if extra else None)
    if df is None or df.empty:
        return None
    columns = list(df.columns)[:TABLE_COLUMNS]
    rows = df[columns].head(TABLE_ROWS).values.tolist()
    total = int((extra or entry).get("rows" if extra else "table_rows") or len(df))
    notes = []
    if total > len(rows):
        notes.append(f"전체 {total:,}행 중 앞 {len(rows)}행")
    if len(df.columns) > TABLE_COLUMNS:
        notes.append(f"전체 {len(df.columns)}열 중 앞 {TABLE_COLUMNS}열")
    return Table([str(c) for c in columns], rows, " · ".join(notes))


def _sources(entry: dict) -> str:
    return ", ".join(SOURCE_LABELS.get(s, s) for s in entry.get("sources") or []) or "-"


def entry_section(entry: dict, number: int, heading: str = "") -> Section:
    blocks = [Paragraph(f"질문: {entry.get('user_question') or entry.get('question')}", "muted")]
    if entry.get("question") and entry.get("question") != entry.get("user_question"):
        blocks.append(Paragraph(f"분석한 질문: {entry['question']}", "muted"))
    blocks.append(Paragraph(f"담당: {entry.get('route_label') or entry.get('route')} · 출처: {_sources(entry)}", "muted"))
    blocks += _answer_blocks(entry.get("answer", ""))
    table = _table_block(entry)
    if table is not None:
        if entry.get("table_title") and entry.get("extra_tables"):
            blocks.append(Paragraph(f"표 1. {entry['table_title']}", "muted"))
        blocks.append(table)
    for number_, extra in enumerate(entry.get("extra_tables") or [], start=2):
        block = _table_block(entry, extra)
        if block is not None:
            blocks.append(Paragraph(f"표 {number_}. {extra.get('title') or ''}".strip(), "muted"))
            blocks.append(block)
    if entry.get("chart_path"):
        blocks.append(Image(entry["chart_path"], entry.get("chart_title") or "그래프"))
    images = [(i["path"], i.get("label") or i.get("article_id", "")) for i in entry.get("images") or [] if i.get("path")]
    if images:
        blocks.append(ImageGrid(images, "상품 이미지 (H&M 원본 이미지, 분석 결과에 나온 상품 중 이미지가 있는 상품)"))
    basis = [b for b in (entry.get("purpose") and f"분석 내용: {entry['purpose']}",
                         entry.get("scope_note") and f"범위: {entry['scope_note']}") if b]
    if basis:
        blocks.append(Bullets(basis))
    if entry.get("sql"):
        blocks.append(Code(entry["sql"], "분석 기준 (SQL)"))
    return Section(heading or f"{number}. {entry.get('user_question') or entry.get('question')}", blocks)


_ARTICLE_ID = re.compile(r"(?<!\d)\d{10}(?!\d)")
MAX_PRODUCT_IMAGES = 6


def _confirmed_product_images(products) -> ImageGrid | Paragraph | None:
    """확정된 주력 상품 값에 들어 있는 H&M 상품 ID(10자리)의 이미지. ID가 없으면 None."""
    ids = list(dict.fromkeys(_ARTICLE_ID.findall(str(products or ""))))
    if not ids:
        return None
    try:
        from agent_system.common_tools.local_data import hm_images
        info = hm_images.lookup(ids)
        files = hm_images.image_files(ids, limit=MAX_PRODUCT_IMAGES)
    except Exception as exc:     # DB·폴더를 열 수 없어도 보고서는 만듦
        return Paragraph(f"주력 상품 이미지를 불러오지 못했습니다 ({type(exc).__name__}).", "muted")
    if not files:
        reasons = sorted({v["image_status"] for v in info.values()})
        return Paragraph("주력 상품 이미지 없음: " + ", ".join(reasons), "muted")
    missing = [i for i in ids if not info.get(i, {}).get("image_available")]
    caption = "주력 상품 이미지 (H&M 원본)" + (f" · 이미지 없음: {', '.join(missing)}" if missing else "")
    return ImageGrid([(f["path"], f["article_id"]) for f in files], caption)


# ---------------------------------------------------------------- 일반 분석 보고서
def build_analysis_report(entries: list[dict], now: datetime | None = None) -> ReportDocument:
    entries = list(entries or [])
    sources = sorted({SOURCE_LABELS.get(s, s) for e in entries for s in e.get("sources") or []})
    summary = Table(["번호", "질문", "담당", "출처"],
                    [[i, e.get("user_question") or e.get("question"), e.get("route_label") or e.get("route"),
                      _sources(e)] for i, e in enumerate(entries, start=1)])
    sections = [Section("분석 목록", [summary])]
    sections += [entry_section(e, i) for i, e in enumerate(entries, start=1)]
    return ReportDocument(
        kind="analysis",
        title="CART SIGNAL 분석 보고서",
        subtitle="대화 중 수행한 분석 결과 모음",
        meta=[("작성 시각", _now_text(now)), ("분석 수", f"{len(entries)}건"), ("데이터 출처", ", ".join(sources) or "-")],
        sections=sections,
        footer=FOOTER,
    )


# ---------------------------------------------------------------- 기획전 실행 계획 보고서
def build_campaign_report(campaign: dict | None, run: dict | None, entries: list[dict],
                          now: datetime | None = None, backend_connected: bool = False) -> ReportDocument:
    """확정값 + 워크플로 진행 + 단계별 결과. 진행하지 않은 단계는 '미진행'으로 그대로 표시합니다."""
    from agent_system.orchestrator.tools.conversation_state import CAMPAIGN_KEYS
    from agent_system.workflows.registry import get_workflow

    values = (campaign or {}).get("values") or {}
    run = run or {}
    sections = []
    notice = ("이 보고서는 대화에 기록된 확정값과 워크플로 단계 결과로 만든 초안입니다. "
              "백엔드 보고서 기능(⑥ 결과 정리 Agent · /reports/campaign) 연결 전이라 content_hash·모델 버전·승인 기록은 포함되지 않습니다.")
    sections.append(Section("작성 기준", [Paragraph(notice, "notice")]))
    sections.append(Section("1. 기획전 확정값", [Table(["항목", "값"], [[label, values.get(key, "미정")]
                                                                  for key, label in CAMPAIGN_KEYS.items()])]))
    product_grid = _confirmed_product_images(values.get("products"))
    if product_grid is not None:
        sections[-1].blocks.append(product_grid)

    workflow = get_workflow(run.get("workflow", ""))
    run_entries = [e for e in entries or [] if (e.get("workflow") or {}).get("run_id") == run.get("run_id")]
    if workflow:
        history = {}
        for item in run.get("history") or []:
            history[(item["no"], item["title"])] = item
        rows, pending = [], []
        steps = workflow.steps()
        for index, (stage, step) in enumerate(steps):
            item = history.get((step.no, step.title))
            if item:
                status = STATUS_LABELS.get(item["status"], item["status"])
            elif index == int(run.get("step_index", 0)) and run.get("status") == "waiting_input":
                status = "마케터 선택 대기"
            elif index == int(run.get("step_index", 0)) and run.get("status") == "blocked":
                status = f"멈춤: {run.get('blocked_reason', '')}"
            else:
                status = "미진행"
            if status in ("미진행",) or status.startswith("멈춤") or status == "마케터 선택 대기":
                pending.append(f"{step.no}단계 {step.title} ({stage.title})")
            rows.append([step.no, stage.title, step.title, step.owner, status])
        sections.append(Section("2. 워크플로 진행", [
            Paragraph(f"{workflow.title} · run {run.get('run_id', '')} · 상태 {run.get('status', '')}", "muted"),
            Table(["단계", "구분", "하는 일", "담당", "상태"], rows)]))
        detail = Section("3. 단계별 결과", [])
        if run_entries:
            sections.append(detail)
            for i, entry in enumerate(run_entries, start=1):
                info = entry.get("workflow") or {}
                sections.append(entry_section(entry, i, f"{info.get('step_no', '')}단계 {info.get('step_title', '')}"))
                sections[-1].level = 2
        else:
            detail.blocks.append(Paragraph("기록된 단계 결과가 없습니다.", "muted"))
            sections.append(detail)
        sections.append(Section("4. 남은 단계", [Bullets(pending) if pending else Paragraph("모든 단계를 마쳤습니다.")]))
    else:
        sections.append(Section("2. 워크플로 진행", [Paragraph(
            "진행한 기획전 워크플로가 없습니다. '기획전 준비 시작하자'로 워크플로를 진행하면 단계별 결과가 들어갑니다.", "muted")]))

    return ReportDocument(
        kind="campaign",
        title="기획전 실행 계획 보고서" + ("" if backend_connected else " (초안)"),
        subtitle=values.get("theme") or values.get("category") or "",
        meta=[("작성 시각", _now_text(now)), ("기획전 일정", values.get("schedule", "미정")),
              ("주력 상품", values.get("products", "미정")), ("타깃", values.get("target", "미정"))],
        sections=sections,
        footer=FOOTER,
    )
