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


def _confirmed_product_images(products, *, reference_only=False) -> ImageGrid | Paragraph | None:
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
    caption = ("과거 판매 참고 사례 이미지 (H&M 원본, 실제 판매 상품 아님)" if reference_only else "주력 상품 이미지 (H&M 원본)") + (f" · 이미지 없음: {', '.join(missing)}" if missing else "")
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

def _json_field(value):
    """상태값은 JSON 문자열로 저장될 수 있으므로 표시 전에 안전하게 해석한다."""
    import json
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            pass
    return {}


def _missing(value):
    return str(value).strip() if value not in (None, '', []) else '미확정'


def _draft_parts(text):
    """Slack에서 확정/수정한 초안의 항목을 재사용하고, 내부 실행 로그는 싣지 않는다."""
    groups: dict[str, list[str]] = {}
    current = None
    for line in str(text or '').splitlines():
        line = line.strip()
        match = re.match(r'^(\d+(?:\.\d+)?)\.\s+(.+)$', line)
        if match:
            current = match.group(1)
            groups[current] = []
        elif current and line:
            groups[current].append(line)
    return groups


def _draft_blocks(lines):
    """완료된 초안에서 확인된 문장만 문단으로 보여준다."""
    return [Paragraph(x, 'muted' if ('[추후' in x or x.startswith('※')) else 'body')
            for x in lines if x and not x.startswith('[초안 상태]')]



def _report_market_evidence(run, draft):
    """저장된 무신사 근거만 보고서에 반영한다. 미확인 숫자는 추정하지 않는다."""
    market = run.get('category_market') or {}
    summary = run.get('campaign_summary') or {}
    evidence = market.get('ranked_evidence') or summary.get('ranked_evidence') or {}
    if not isinstance(evidence, dict):
        evidence = {}

    # 집계의 출처와 비교 범위를 명시한 구조화 근거가 가장 우선이다.
    total = evidence.get('total_products')
    eligible = evidence.get('eligible_products')
    related = evidence.get('related_products')
    reviews = evidence.get('related_reviews')
    subtype = evidence.get('subtype') or run.get('selected_subtype') or '선택 세부 유형'
    if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
           for v in (total, eligible, related, reviews)):
        parts = [f"무신사 {market.get('category') or summary.get('category') or run.get('review_category') or '해당 카테고리'} 랭킹 수집 상품 {total}개 중 여성·공용 비교 대상 "
                 f"{eligible}개, '{subtype}' 관련 상품명 기반 잠정 후보 "
                 f"{related}개 및 연결 리뷰 {reviews}건을 확인했습니다."]
        shapes = {str(item.get('label')): item.get('products')
                  for item in evidence.get('related_shapes') or [] if isinstance(item, dict)}
        designs = [f'{label} {shapes[label]}개' for label in ('후드형', '푸퍼형', '크롭형')
                   if isinstance(shapes.get(label), int) and shapes[label] >= 0]
        if designs:
            parts.append('관련 상품명에서 관찰된 디자인 표현(중복 허용): ' + ', '.join(designs) + '.')
        parts.append('랭킹은 매출·선호도 순위가 아니며, 상품명 분류는 잠정적이고 리뷰 언급은 긍정 평가가 아닙니다.')
        return parts

    # 구조화 근거가 없다면 보고서 초안의 원래 무신사 설명만 재사용한다.
    # 부정확한 개수를 기본값으로 넣거나 새로운 API·DB 조회는 하지 않는다.
    return [line for line in draft.get('2', []) if line.startswith('무신사')]


def _market_detail_blocks(run: dict) -> list:
    """저장된 관련 상품 표본의 수치만 기록한다. 집계하지 않은 값은 만들지 않는다."""
    market = run.get('category_market') or {}
    summary = run.get('campaign_summary') or {}
    ranked = market.get('ranked_evidence') or summary.get('ranked_evidence') or {}
    if not isinstance(ranked, dict):
        return []
    blocks = []
    stats = ranked.get('market_metrics') or {}
    if isinstance(stats, dict) and isinstance(stats.get('metrics'), dict):
        from shared_analysis.market.price_quality import render_metrics
        blocks.append(Paragraph('3.4 관련 상품 가격·할인·평점·리뷰 통계', 'subheading'))
        blocks.extend(Paragraph(line) for line in render_metrics(stats))
    aspects = [x for x in ranked.get('aspects') or [] if isinstance(x, dict)
               and isinstance(x.get('mentions'), int) and not isinstance(x.get('mentions'), bool)
               and x.get('mentions') >= 0 and x.get('label')]
    if aspects:
        denominator = ranked.get('related_reviews')
        blocks.append(Paragraph('3.5 관련 상품 수집 리뷰 관심 표현', 'subheading'))
        rows = []
        for item in aspects:
            n = item['mentions']
            rate = f'{n / denominator:.1%}' if isinstance(denominator, int) and denominator > 0 else '분모 미확인'
            rows.append([str(item['label']), f'{n:,}건', rate])
        blocks.append(Table(['리뷰 관심사', '언급 리뷰 수', '수집 리뷰 대비'], rows,
                            '복수 관심사가 한 리뷰에 등장할 수 있으며 긍정·부정 평가를 구별하지 않습니다.'))
    return blocks


def _cross_evidence_blocks(run: dict, confirmed_ids: list[str], *, reference_only=False, selected_points=None) -> list:
    """실제 확정 상품 ID가 포함된 H&M·무신사 교차 근거만 명확히 구분한다."""
    summary = run.get('campaign_summary') or {}
    cross = summary.get('cross_evidence') or []
    if not isinstance(cross, list):
        return []
    rows = []
    approved = set(confirmed_ids)
    for item in cross:
        if not isinstance(item, dict) or item.get('cross_verified') is not True:
            continue
        ids = item.get('hm_matched_ids') or []
        if not isinstance(ids, list) or not ids or not all(x in approved for x in ids):
            continue
        mentions, total = item.get('musinsa_mentions'), item.get('musinsa_related_reviews')
        if not (isinstance(mentions, int) and not isinstance(mentions, bool)
                and isinstance(total, int) and total > 0 and 0 <= mentions <= total):
            continue
        scope = '상품별 한정' if item.get('scope') == 'partial' else '공통'
        rows.append([('[선택] ' if str(item.get('label') or '') in (selected_points or []) else '[미선택·참고] ') + str(item.get('label') or '확인된 특징'), scope,
                     ', '.join(ids), f"{item.get('musinsa_aspect') or '관심 표현'} {mentions:,}/{total:,}건"])
    if not rows:
        return []
    return [Paragraph('5.2 H&M × 무신사 참고 소구점 교차 근거 (미선택 항목 포함)' if reference_only else '5.2 H&M × 무신사 소구점 교차 근거', 'subheading'),
            Table(['소구점', '과거 사례 일치 범위' if reference_only else 'H&M 적용 범위', 'H&M 과거 참고 ID' if reference_only else '확인된 H&M 상품 ID', '무신사 리뷰 언급'], rows),
            Paragraph('무신사 리뷰는 경쟁 상품의 관심 표현이며 H&M 상품의 만족도·성능 또는 동일 상품을 입증하지 않습니다.', 'notice')]


def _approved_copy_blocks(page: dict) -> list:
    """최종 승인된 카피의 제목·본문을 하나의 완성형 카피로 표시한다."""
    headline = str(page.get('headline') or '').strip()
    subcopies = [str(x).strip() for x in (page.get('subcopies') or [])
                 if isinstance(x, str) and x.strip()]
    blocks = [Paragraph('5.3 승인된 완성형 캠페인 카피', 'subheading'),
              Paragraph(headline or '미확정', 'subheading'),
              *[Paragraph(text) for text in subcopies]]
    keywords = page.get('keywords') or []
    valid = [k.strip() for k in keywords if isinstance(k, str) and k.strip()] if isinstance(keywords,list) else []
    if valid:
        blocks.append(Paragraph('배너·상세페이지 키워드: ' + ' · '.join(valid[:5])))
    memo = page.get('review_memo') or []
    if isinstance(memo, list) and memo:
        blocks.append(Paragraph('5.4 마케터 검토 메모 — 고객 광고 비노출', 'subheading'))
        blocks.extend(Paragraph(str(x), 'notice') for x in memo if isinstance(x,str) and x.strip())
    return blocks


def _deduplicate_purchase_summary(extra, total_note):
    """Word의 집계 합계와 동일한 초안 문장을 레거시/참고 사례 모두에서 제외한다."""
    if '합계:' not in total_note:
        return list(extra)
    labels = ('H&M 확정 상품', '확정 상품', '과거 참고 사례', 'H&M 과거 구매 사례')
    return [line for line in extra
            if not ('구매 기록 합계' in line and any(label in line for label in labels))]


def build_campaign_report(campaign: dict | None, run: dict | None, entries: list[dict],
                          now: datetime | None = None, backend_connected: bool = False) -> ReportDocument:
    """마케터 검토용 제안서. 승인된 Run 스냅샷과 수정된 초안을 우선 사용한다.

    내부 JSON/단계별 실행 기록은 Word 본문에 노출하지 않는다. 고객군과 발송 수치는
    모델이 연결되기 전까지 실제 결과로 채우지 않는다.
    """
    from agent_system.orchestrator.tools import provisional_report

    values = (campaign or {}).get('values') or {}
    run = run or {}
    # 동일 Run의 검증된 승인 카피가 존재하면 보고서 생성 직전 재검사한다.
    # legacy Run의 미검증 상태를 PASS로 표현하지는 않는다.
    from agent_system.validation.campaign_evidence_gate import require_approved_copy_for_report
    require_approved_copy_for_report(run, campaign or {})
    page = _json_field(values.get('page'))
    selling = _json_field(values.get('selling_points'))
    confirmed_ids = list(dict.fromkeys(_ARTICLE_ID.findall(str(values.get('products') or ''))))
    rows_by_id = {str(row.get('article_id')): row for row in run.get('verified_candidate_rows') or []
                  if isinstance(row, dict)}
    colors = values.get('colors') or []
    if isinstance(colors, str):
        colors = [x.strip() for x in colors.split(',') if x.strip()]
    title = str(page.get('headline') or '').strip() or '미확정'
    points = selling.get('points') or []
    theme = selling.get('theme') or values.get('theme') or values.get('subtype') or '미확정'
    from agent_system.orchestrator.tools.category_planning.period_context import (
        period_note, purchase_label, run_schedule, validate_report_context, saved_comparison_note,
    )
    validate_report_context(run, campaign)
    plan = run.get('period_plan')
    basis = purchase_label(plan)
    schedule = run_schedule(run, _missing(values.get('schedule')))
    from agent_system.orchestrator.tools.merchandising_direction import is_reference, build_direction
    reference_only = is_reference(run) or is_reference(run.get('campaign_summary'))
    direction = (selling.get('merchandising_direction') or
                 build_direction(run.get('campaign_summary') or {}, selling,
                     subtype=str(values.get('subtype') or ''), colors=colors)) if reference_only else None

    # report_draft는 사용자가 Slack에서 보완한 최신 버전이다. 이를 그대로 재사용한다.
    saved = (run.get('report_draft') or {}).get('text')
    if not saved:
        try:
            saved = provisional_report.build_draft(run, campaign)
        except ValueError:
            saved = ''
    draft = _draft_parts(saved)

    products = []
    purchases = []
    for aid in confirmed_ids:
        row = rows_by_id.get(aid, {})
        count = row.get('winter_online_purchases')
        valid = isinstance(count, (int, float)) and not isinstance(count, bool) and float(count).is_integer() and count >= 0
        if valid:
            purchases.append(int(count))
        products.append([str(row.get('prod_name') or '상품명 확인 필요'), aid,
                         str(row.get('colour_group_name') or '확인 필요'),
                         f'{int(count):,}건' if valid else '미확인'])
    total_note = (f'{"과거 참고 사례" if reference_only else "확정 상품"} {len(confirmed_ids)}개의 {basis} 기록 합계: {sum(purchases):,}건'
                  if confirmed_ids and len(purchases) == len(confirmed_ids) else
                  '과거 참고 사례의 구매 건수 중 미확인 값이 있어 합계는 표시하지 않습니다.' if reference_only else '확정 상품 구매 건수 중 미확인 값이 있어 합계는 표시하지 않습니다.')

    # 수정된 초안의 2.1절을 먼저 사용하며, 아직 재생성 전이면 Run 근거로 보완한다.
    extra = draft.get('2.1') or []
    if not extra:
        verified = []
        if hasattr(provisional_report, '_verified_naver_note'):
            verified.append(provisional_report._verified_naver_note(run, campaign))
        if confirmed_ids:
            verified.append(total_note + ' (과거 기록이며 현재 판매량 아님)')
        extra = verified

    # Slack 초안에 추가된 무신사 관심사별 수정 결과도 Word에 전달한다.
    musinsa_extra = []
    for line in extra:
        if line.startswith('- 무신사') or line.startswith('- 핏·') or line.startswith('- 가벼움') or line.startswith('- 색감') or line.startswith('- 보온성') or line.startswith('- 디자인') or line.startswith('- 두께감') or line.startswith('- 리뷰'):
            musinsa_extra.append(line)
    # 무신사 상세 통계는 아래 구조화 표로 이미 출력되므로 중복 문단으로 표시하지 않는다.
    extra = [x for x in extra if x not in musinsa_extra]

    # 문서 본문에서는 구매 합계를 한 번만 보여주고, 무신사 근거를 별도로 구성한다.
    extra = _deduplicate_purchase_summary(extra, total_note)
    musinsa_notes = _report_market_evidence(run, draft)

    sections = [
        Section('1. 제안 요약', [
            Paragraph(f'기획전: {theme} / 세부 유형: {_missing(values.get("subtype"))}'),
            Paragraph(f'대표 문구: {title}'),
            Paragraph('과거 H&M 상품은 디자인 참고 사례이며, 유형·색상·디자인 특징을 향후 기획전 편성 조건으로 제안합니다. 현재 판매 상품·타깃 선정·발송은 미확정입니다.' if reference_only else '확정된 상품·색상·콘텐츠를 바탕으로 한 기획안이며 타깃 선정·발송은 미완료입니다.', 'notice')]),
        Section('2. 기획전 기본안', [Table(['항목', '제안 내용'], [
            ['기획전 형태', '온라인 회원 대상 기획전 (기획 목표)' if plan else '온라인 회원 대상 겨울 기획전 (기획 목표)'],
            ['대분류 / 세부 유형', f'{_missing(values.get("category"))} / {_missing(values.get("subtype"))}'],
            ['기획전 주제', theme],
            ['운영 기간', schedule],
            ['색상', ', '.join(colors) if colors else '미확정'],
            ['상품 수', (f'{len(confirmed_ids)}개 (과거 판매 참고 사례; 실제 편성 상품 미확정)' if reference_only else f'{len(confirmed_ids)}개 (확정 상품 ID 기준)')],
            ['대표 문구', title],
            ['실제 발송 규모', '추후 작성 (타깃 모델 미연결)']])]),
        Section('3. 상품 선정 및 시장 분석 근거', [
            Paragraph('3.1 H&M 과거 구매 근거', 'subheading'),
            Paragraph('H&M 과거 20~39세 고객·여성용 상품·' + ('검증된 핵심 월' if plan else '겨울(12~2월)') + '·온라인 구매 기록을 참고했습니다. 고객 성별과 현재 수요·재고는 확인되지 않았습니다.'),
            *([Paragraph(line) for line in period_note(plan).splitlines() if not line.startswith("운영 일정:")] if plan else []),
            *([Paragraph(saved_comparison_note(run))] if saved_comparison_note(run) else []),
            Paragraph(total_note + ' (현재 판매량 아님)'),
            Paragraph("3.2 네이버 및 추가 분석 근거", "subheading"),
            *(_draft_blocks(extra) or [Paragraph("추가 분석 근거 미확인", "notice")]),
            Paragraph("3.3 무신사 경쟁 시장 근거", "subheading"),
            *(_draft_blocks(musinsa_notes) or [Paragraph("무신사 경쟁 시장 근거 미확인", "notice")]),
            *_market_detail_blocks(run),
            *_draft_blocks([x for x in draft.get('2', []) if x.startswith('선정 근거:')])]),
        Section('4. 과거 H&M 판매 참고 사례 (실제 판매 상품 미확정)' if reference_only else '4. 주력 상품 구성', [
            Table(['과거 상품명' if reference_only else '상품명', '참고 상품 ID' if reference_only else '상품 ID', '색상', basis], products)
            if products else Paragraph('참고 상품 사례가 없습니다.' if reference_only else '확정된 상품 ID가 없습니다.', 'notice')]),
        Section('5. 상품 구성 전략 및 콘텐츠' if reference_only else '5. 콘텐츠 및 소구점', [
            *([Paragraph('5.1 향후 상품 편성 조건', 'subheading'), Paragraph(f"상품 유형: {direction.get('subtype') or '미확정'} / 검토 색상: {', '.join(direction.get('colors') or []) or '미확정'}"), Paragraph('검토 디자인 특징: ' + (' / '.join(x['feature'] for x in direction.get('design_features') or []) or '미선정')), Paragraph('판매 상품 ID·재고·실제 적용 디자인 특징: 미확정 (과거 H&M 상품 ID를 판매 상품으로 사용하지 않음)', 'notice')] if reference_only else []),
            Paragraph(('편성 방향으로 선택한 소구점: ' if reference_only else '확정 소구점: ') + (', '.join(str(p) for p in points) if points else '미확정')),
            *_cross_evidence_blocks(run, confirmed_ids, reference_only=reference_only, selected_points=points),
            *_approved_copy_blocks(page),
            Paragraph(f'{"·".join(colors)} 과거 상품 사례를 비교하고, 향후 실제 편성 상품에는 확인된 디자인만 반영합니다.' if reference_only else f'{"·".join(colors)} 상품을 비교하고, 확인된 상품별 디자인 차이를 안내합니다.'
                      if colors else '상품별로 실제 확인된 색상·디자인 차이만 안내합니다.'),
            Paragraph('과거 상품의 소구점·디자인은 향후 편성 조건의 참고 근거일 뿐 실제 판매 상품의 성능을 입증하지 않습니다. 미확인 소재·보온성·핏·재고·가격·할인·혜택은 확정 표현으로 사용하지 않습니다.' if reference_only else '미확인 보온성·핏·착용감·가격·할인·혜택은 확정적 광고 문구로 사용하지 않습니다.', 'notice')]),
        Section('6. 고객 타깃 선정 및 메시지 운영 — 추후 작성', [
            Paragraph('실제 고객 점수·세그먼트 인원·발송 명단은 생성하지 않았습니다. 아래 항목은 팀원 모델 연결 후 채울 공간입니다.', 'notice')]),
    ]
    pictures = _confirmed_product_images(values.get('products'), reference_only=reference_only)
    if pictures is not None:
        sections[3].blocks.append(pictures)

    target = sections[5].blocks
    target.extend([
        Paragraph('6.1 핵심 타깃 정의 및 선정 조건'),
        Table(['항목', '확인 결과'], [
            ['기획상 목표', '20~39세 온라인 회원 (성별 확인값 아님)'],
            ['적용 필터·데이터 기준일', '________________ [추후 입력]'],
            ['회원 상태·마케팅 수신 동의', '________________ [추후 입력]']]),
        Paragraph('6.2 고객 점수 및 세그먼트별 선정 결과'),
        Paragraph('모델명·버전·점수 산정 기준: ____________________ [추후 입력]'),
        Table(['세그먼트', '인원', '선정/제외 기준'], [
            ['핵심 타깃', '____명', '________________'],
            ['재활성화 후보', '____명', '________________'],
            ['교차 판매 후보', '____명', '________________'],
            ['제외 고객', '____명', '________________']]),
        Paragraph('6.3 발송 규모 및 연령대 구성'),
        Paragraph('최종 발송 가능 인원: ____명 / 20대: ____명 / 30대: ____명'),
        Paragraph('발송 채널·연락처 확인: ____________________ [추후 입력]'),
        Paragraph('6.4 대조군 및 실험 설계'),
        Paragraph('대조군 인원·비율: ____________________ / 색상 노출·메시지 실험: ____________________'),
        Paragraph('6.5 고객군별 메시지 초안'),
        Table(['고객군', '메시지 초안'], [['핵심 타깃', '________________________'],
                                  ['재활성화', '________________________'],
                                  ['교차 판매', '________________________']]),
        Paragraph('6.6 발송 전 검증 및 승인'),
        Paragraph('수신 동의·중복 제거·수신 거부 확인: ____________________'),
        Paragraph('담당자 검토 및 최종 승인: ____________________'),
    ])
    sections.extend([
        Section('7. 운영 및 성과 측정 계획', [
            Paragraph('실제 발송 전 최신 회원·재고·가격·URL·발송 채널·마케팅 수신 동의를 검증해야 합니다.'),
            Paragraph('성과 지표(계획): 발송 성공률, 클릭률, 상세 조회, 장바구니, 구매 전환율, 수신 거부율. 현재 목표 및 실측 수치는 없습니다.')]),
        Section('8. 데이터 한계 및 검증 필요 항목', [
            Bullets(['H&M 거래 기록은 과거 자료이며 2026년 현재 판매량·재고가 아닙니다.',
                     '무신사 수집 랭킹과 리뷰 언급 빈도는 판매량·선호도·H&M 제품 성능의 근거가 아닙니다.',
                     '네이버 상대 관심도는 절대 판매량이나 캠페인 기간 수요를 보장하지 않습니다.',
                     '실제 재고·가격·할인·상품 URL·회원 연락처·마케팅 수신 동의·기간을 확인해야 합니다.'])]),
        Section('9. 팀장 검토 요청 사항', [
            Paragraph(f'우선 검토: 기획전 유형·색상·디자인 편성 조건, 과거 참고 사례 {len(confirmed_ids)}개, 대표 문구 및 소구점.' if reference_only else f'우선 검토: 기획전 주제, 확정 상품 {len(confirmed_ids)}개, 선정 색상, 최종 대표 문구와 소구점.'),
            Paragraph('후속 보완: 실제 판매 상품 SKU·소재·디자인 근거·재고·가격·혜택·URL·고객 타깃 선정·발송 가능 명단·운영 기간.' if reference_only else '후속 보완: 고객 타깃 선정 결과·발송 가능 명단·운영 기간·가격·재고·혜택·URL.'),
            Paragraph('이 문서는 기획안 검토용 초안이며 실제 발송 승인·메일 발송·페이지 공개가 이뤄졌다는 뜻이 아닙니다.', 'notice')]),
    ])
    return ReportDocument(
        kind='campaign', title='기획전 타깃 및 운영 제안서 (초안)', subtitle=str(theme),
        meta=[('작성 시각', _now_text(now)), ('운영 기간', schedule),
              ('주력 유형', _missing(values.get('subtype'))), ('타깃 분석', '모델 연결 전 — 추후 작성')],
        sections=sections,
        footer='CART_SIGNAL · 확인된 과거 데이터와 마케터 확정값 기준 · 타깃/발송 미실행 초안',
    )
