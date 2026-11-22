"""타깃 모델 미연결 시 안전하게 생성하는 기획전 운영 제안서 초안.

기존 17~29단계의 실제 실행/승인과 독립적이다. 발송 가능한 고객을 만들지 않는다.
"""
from __future__ import annotations

import json
import re


def _text(value, default="미확정"):
    if value is None or value == "" or value == []:
        return default
    return str(value).strip() or default


def _full_copy(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return '미확정'
    if not isinstance(value, dict):
        return '미확정'
    segments = [str(value.get('headline') or '').strip()]
    segments.extend(str(x).strip() for x in (value.get('subcopies') or []) if str(x).strip())
    return '\n'.join(x for x in segments if x) or '미확정'


def _chosen_copy(value):
    if isinstance(value, dict):
        return _text(value.get("headline"))
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return "미확정"
    return _text(parsed.get("headline")) if isinstance(parsed, dict) else "미확정"


def _selling_points(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, dict):
        return []
    points = value.get("points") or []
    return [str(x).strip() for x in points if str(x).strip()]


def _initial_evidence(run: dict, campaign: dict) -> list[str]:
    """첫 초안에 포함할 필수 근거. 해당 Run에 저장된 검증값만 사용한다."""
    market = run.get('category_market') or {}
    summary = run.get('campaign_summary') or {}
    ranked = market.get('ranked_evidence') or summary.get('ranked_evidence') or {}
    rows = []

    naver = _verified_naver_note(run, campaign)
    rows.append('네이버 트렌드: ' + naver)
    if isinstance(ranked, dict) and ranked.get('related_reviews'):
        rows.extend('무신사 리뷰: ' + line if i == 0 else '  ' + line
                    for i, line in enumerate(_verified_musinsa_note(run)))
    else:
        rows.append('무신사 리뷰 상세: 검증된 관심사별 집계가 없어 확인이 필요합니다.')

    # 중복 ID나 결측 구매량은 추정하지 않고 별도로 안내한다.
    purchase = _verified_purchase_total(run, campaign)
    rows.append('H&M 과거 구매 사례: ' + purchase)

    # 세 출처는 각각 다른 의미를 갖는다. 자동 추천을 인과적 사실로 표현하지 않는다.
    rows.append('추천 판단: 네이버는 수집 키워드의 상대 관심도, H&M은 과거 구매 및 상품 설명, '
                '무신사는 경쟁 상품명·리뷰 관심사에 대한 참고 근거입니다. '
                '현재 판매량·소재 성능·재고·기획전 기간 수요를 입증하지 않습니다.')
    return rows


def build_draft(run, campaign):
    """확정 정보만 사용해 보고서 초안을 만든다. 타깃 수·성과·발송을 추정하지 않는다."""
    values = (campaign or {}).get("values") or {}
    if not values.get("products") or _chosen_copy(values.get("page")) == "미확정":
        raise ValueError("과거 참고 사례·상품 구성 방향과 대표 문구를 먼저 확정해야 보고서 초안을 만들 수 있습니다.")
    ids = re.findall(r"(?<!\d)\d{10}(?!\d)", str(values.get("products")))
    if not ids:
        raise ValueError("실제 H&M 상품 ID가 없어 보고서 초안을 만들 수 없습니다.")
    colors = values.get("colors") or []
    if isinstance(colors, str):
        colors = [x.strip() for x in colors.split(",") if x.strip()]
    subtype = _text(values.get("subtype"), _text(values.get("category")))
    from agent_system.orchestrator.tools.category_planning.period_context import (
        period_note, purchase_label, run_schedule, validate_report_context, saved_comparison_note,
    )
    validate_report_context(run, campaign)
    plan = run.get('period_plan')
    theme = _text(values.get("theme"), f"{'' if plan else '겨울 '}{_text(values.get('category'))} 컬렉션")
    schedule = run_schedule(run, _text(values.get("schedule")))
    basis = purchase_label(plan)
    points = _selling_points(values.get("selling_points"))
    rows = {str(row.get("article_id")): row for row in run.get("verified_candidate_rows") or [] if isinstance(row, dict)}
    product_lines = []
    for num, aid in enumerate(ids, 1):
        r = rows.get(aid, {})
        name = _text(r.get("prod_name") or r.get("product_name"), "상품명 확인 필요")
        color = _text(r.get("colour_group_name") or r.get("color"), "색상 확인 필요")
        count = r.get("winter_online_purchases")
        purchases = f"{basis} {int(count):,}건" if isinstance(count, (int,float)) else "구매 건수 확인 필요"
        product_lines.append(f"{num}. {name} / {aid} / {color} / {purchases}")
    market = run.get("category_market") or {}
    ranked = market.get("ranked_evidence") or (run.get("campaign_summary") or {}).get("ranked_evidence") or {}
    market_note = "무신사 상위 랭킹 및 관련 리뷰: 해당 데이터의 수집 시점·표본·분류 한계를 확인해야 합니다."
    if ranked:
        market_note = (f"무신사 {market.get('category') or values.get('category') or '해당 카테고리'} 상위 {ranked.get('total_products', '미확인')}개 수집 표본에서 여성·공용 상품의 관련 잠정 후보 "
                       f"{ranked.get('related_products', '미확인')}개와 연결 리뷰 "
                       f"{ranked.get('related_reviews', '미확인')}건을 참고했습니다. "
                       "상품명 기반 잠정 분류이며 판매량·시장 선호도 순위가 아닙니다.")
    initial_evidence = _initial_evidence(run, campaign)
    return (f"■ 기획전 운영 제안서 초안 (타깃 모델 연결 전)\n"
            f"\n1. 제안 요약\n기획전: {theme}\n"
            f"대분류·소분류: {_text(values.get('category'))} / {subtype}\n"
            f"선정 색상: {', '.join(colors) if colors else '미확정'}\n"
            f"기간: {schedule}\n"
            f"승인된 완성형 카피:\n{_full_copy(values.get('page'))}\n"
            "\n2. H&M 과거 판매 참고 사례 및 근거 (현재 판매 상품 아님)\n"
            + "\n".join(product_lines) + "\n"
            "참고 사례 선정 근거: H&M 상품명·설명 기준 세부 유형 매칭과 20~39세·여성용 상품·"
            + ("검증된 핵심 월" if plan else "겨울(12~2월)") + "·온라인 채널의 과거 거래 기록."
            + " 현재 매출이나 재고는 아닙니다.\n"
            + ((period_note(plan) + "\n" + saved_comparison_note(run) + "\n") if plan else "") +
            f"{market_note}\n"
            "\n2.1. 추가 분석 근거\n" + "\n".join('- ' + line for line in initial_evidence) + "\n"
            "\n3. 제안 상품 구성 조건 및 콘텐츠 소구점\n"
            + ("편성 특징 후보: " + ", ".join(points) if points else "편성 특징 후보: 확인 필요") + "\n"
            "과거 사례에서 확인된 색상·디자인 특징을 향후 상품 편성 조건으로 제안합니다. 실제 판매 상품의 소재·성능은 아직 검증하지 않았습니다.\n"
            "\n4. 고객 타깃 선정 및 메시지 운영 — 추후 작성\n"
            "[작성 예정] 이 절은 타깃 선정 모델 및 최신 운영 데이터 연동 후 채울 공간입니다. "
            "아래 빈칸은 실제 분석 결과가 아니며, 고객 선정·발송을 수행하지 않았습니다.\n"
            "4.1. 핵심 타깃 정의 및 선정 조건\n"
            "기획상 목표 고객: 20~39세 온라인 회원 (여성 의류 구매 이력은 참고 조건이며 고객 성별 확인값이 아님)\n"
            "실제 적용 필터·데이터 기준일: ____________________ [추후 입력]\n"
            "ACTIVE 상태·마케팅 수신 동의 확인: ____________________ [추후 입력]\n"
            "4.2. 고객 점수 및 세그먼트별 선정 결과\n"
            "모델명·버전·점수 산정 기준: ____________________ [추후 입력]\n"
            "핵심 타깃 / 대상자 수: ______명 / 선정 기준: ____________________\n"
            "재활성화 후보 / 대상자 수: ______명 / 선정 기준: ____________________\n"
            "교차 판매 후보 / 대상자 수: ______명 / 선정 기준: ____________________\n"
            "제외 고객 / 대상자 수: ______명 / 제외 사유: ____________________\n"
            "※ 위 세그먼트 명칭은 설계용 목차이며 실제 고객 분류 결과가 아닙니다.\n"
            "4.3. 발송 규모 및 연령대 구성\n"
            "최종 발송 가능 인원: ______명 / 20대: ______명 / 30대: ______명\n"
            "발송 채널·연락 가능 여부: ____________________ [추후 입력]\n"
            "4.4. 대조군 및 실험 설계\n"
            "대조군 비율·인원: ____________________ [추후 입력]\n"
            "색상별 노출 또는 메시지 실험 조건: ____________________ [추후 입력]\n"
            "4.5. 고객군별 메시지 초안\n"
            "핵심 타깃: ________________________________ [추후 작성]\n"
            "재활성화 후보: ____________________________ [추후 작성]\n"
            "교차 판매 후보: __________________________ [추후 작성]\n"
            "4.6. 발송 전 검증 및 승인\n"
            "최신 대상자·연락처·수신 동의·수신 거부·중복 제거 확인: __________ [추후 입력]\n"
            "담당자 검토 및 최종 승인: ____________________ [추후 입력]\n"
            "※ 실제 고객 점수·세그먼트 인원·발송 대상 수는 미확정이며, 고객별 메시지 발송은 금지합니다.\n"
            "\n5. 운영 및 성과 확인 계획\n"
            "보고서 승인 후 최신 고객·재고·가격·상품 URL·마케팅 수신 동의·발송 채널을 재확인해야 합니다. "
            "향후 발송 성공률·클릭률·상세 조회·장바구니·구매전환율·수신 거부율을 측정할 수 있습니다. "
            "목표 수치와 실제 성과는 아직 없습니다.\n"
            "\n6. 팀장 검토 요청 전 확인 항목\n"
            "상품 유형·색상·디자인 방향과 대표 문구를 검토했지만, 실제 판매 상품·재고·가격·혜택·실제 발송 대상·마케팅 수신 동의·URL은 별도 확인이 필요합니다.\n"
            "\n[초안 상태] 타깃 모델 미연결로 실행 계획의 일부만 작성됐습니다. "
            "팀장 승인 메일 발송, 실제 고객 선정·메시지 발송, 페이지 공개 및 사후 예약은 수행하지 않았습니다. "
            "기존 워크플로는 고객 타깃 분석 진입 전 상태를 유지합니다.")


def is_report_revision(message: str) -> bool:
    """기존 보고서에 대한 수정 요청을 일반 데이터 분석과 구분한다."""
    text = str(message or "").strip()
    return bool(re.search(r"(?:기획전\s*)?보고서", text)
                and re.search(r"추가|수정|보완|반영|업데이트|고쳐|바꿔", text))


def _revision_intents(message: str) -> set[str]:
    """검증된 자료로 처리 가능한 보고서 수정 요청의 항목을 분리한다."""
    text = str(message or '')
    intents = set()
    if re.search(r'네이버|트렌드|관심도', text):
        intents.add('naver')
    if re.search(r'구매\s*(?:기록|건수|합계)|구매량|상품\s*5개.*합계', text):
        intents.add('purchase')
    if re.search(r'무신사|리뷰|언급\s*(?:건수|비율)|관심사', text):
        intents.add('musinsa')
    return intents


def _supported_revision(message: str) -> bool:
    return bool(_revision_intents(message))


def _verified_musinsa_note(run: dict) -> list[str]:
    """저장된 관련 상품 리뷰 집계만 재사용한다. 없으면 누락 사유를 표시한다."""
    market = run.get('category_market') or {}
    summary = run.get('campaign_summary') or {}
    ranked = market.get('ranked_evidence') or summary.get('ranked_evidence') or {}
    if not isinstance(ranked, dict):
        ranked = {}
    count = ranked.get('related_reviews')
    aspects = ranked.get('aspects') or []
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0 or not isinstance(aspects, list):
        return ['무신사 관련 리뷰의 검증된 건수 또는 관심사 집계가 없어 상세 표를 추가하지 않았습니다.']
    valid = [a for a in aspects if isinstance(a, dict) and a.get('label')
             and isinstance(a.get('mentions'), int) and not isinstance(a.get('mentions'), bool)
             and 0 <= a['mentions'] <= count]
    if not valid:
        return ['무신사 관심사별 언급 건수가 검증되지 않아 상세 표를 추가하지 않았습니다.']
    rows = [f"{a['label']}: {a['mentions']:,}건 ({a['mentions'] / count:.1%})"
            for a in valid]
    return [f'무신사 관련 잠정 상품의 연결 리뷰 {count:,}건 기준 관심사별 언급 (중복 집계 가능):',
            *rows, '리뷰 언급은 긍정 평가나 판매량이 아니며, 과거 H&M 상품에 대한 직접 후기가 아닙니다.']


def _prior_additions(report: str) -> list[str]:
    match = re.search(r'\n2\.1\. 추가 분석 근거\n(.*?)(?=\n\n3\. 제안 상품 구성 조건)',
                      report or '', flags=re.S)
    return [x.strip() for x in match.group(1).splitlines() if x.strip()] if match else []


def _verified_purchase_total(run: dict, campaign: dict) -> str:
    """승인된 ID만 합산하고 결측 시 합계를 추정하지 않는다."""
    values = (campaign or {}).get("values") or {}
    ids = re.findall(r"(?<!\d)\d{10}(?!\d)", str(values.get("products") or ""))
    rows = {str(row.get("article_id")): row for row in run.get("verified_candidate_rows") or []
            if isinstance(row, dict)}
    if not ids or len(set(ids)) != len(ids):
        return "확정된 상품 ID 목록의 검증이 필요하여 합계를 계산하지 않았습니다."
    counts = []
    for aid in ids:
        count = rows.get(aid, {}).get("winter_online_purchases")
        if isinstance(count, bool) or not isinstance(count, (int, float)) or not float(count).is_integer() or count < 0:
            return "선정 상품 중 구매 기록이 확인되지 않은 항목이 있어 합계를 계산하지 않았습니다."
        counts.append(int(count))
    from agent_system.orchestrator.tools.category_planning.period_context import purchase_label
    return (f"H&M 과거 참고 사례 {len(ids)}개({', '.join(ids)})의 {purchase_label(run.get('period_plan'))} 기록 합계: "
            f"{sum(counts):,}건. 동일 Run의 승인 상품만 합산했으며 현재 판매량이나 해당 유형 전체 구매량은 아닙니다.")


def _verified_naver_note(run: dict, campaign: dict) -> str:
    """저장된 키워드별 상대 변화율과 비교 기간만 보고한다."""
    values = (campaign or {}).get("values") or {}
    subtype = _text(values.get("subtype"), "")
    metrics = ((run.get("category_market") or {}).get("naver_metrics") or {})
    key = subtype if subtype in metrics else subtype.replace(" ", "")
    entry = metrics.get(key)
    if not isinstance(entry, dict):
        return (f"선정 유형 '{subtype or '미확정'}'에 대응하는 네이버 저장 근거가 없어 "
                "관심도 변화율을 추가하지 않았습니다.")
    growth = entry.get("growth_percent")
    if isinstance(growth, bool) or not isinstance(growth, (int, float)):
        return "네이버 상대 관심도 변화율이 저장되지 않아 수치 제시를 보류합니다."
    prior = entry.get("prior_window") or "기간 미확인"
    recent = entry.get("recent_window") or "기간 미확인"
    return (f"네이버 '{key}' 상대 관심도 변화 {growth:+.2f}% "
            f"(이전: {prior} / 최근: {recent}). "
            "동일 키워드의 비교 기간 내 상대 지표 변화이며 절대 검색량·현재 구매량·향후 수요 예측은 아닙니다.")


def revise_draft(run: dict, campaign: dict, message: str) -> str:
    """보고서 확정값 및 기존 보강 근거를 유지하면서 복수 요청을 모두 반영한다."""
    intents = _revision_intents(message)
    if not intents:
        raise ValueError('현재는 네이버·무신사 저장 근거 및 H&M 구매 합계 보강만 지원합니다. 기존 보고서는 유지됩니다.')
    # 지원하지 않는 수정을 함께 요청하면 일부만 조용히 반영하지 않는다.
    if re.search(r'글꼴|폰트|표지\s*(?:디자인|변경)|레이아웃\s*변경|차트\s*(?:추가|수정)|발송\s*(?:대상|명단)|재고\s*(?:수량|현황)', str(message or '')):
        raise ValueError('요청 중 현재 처리할 수 없는 수정 항목이 있습니다. 초안은 유지됩니다. '
                         '네이버·무신사 저장 근거 또는 H&M 구매 합계 보강 요청으로 나누어 주세요.')
    previous = (run.get('report_draft') or {}).get('text')
    if not previous:
        raise ValueError("수정할 보고서 초안이 없습니다. 먼저 '기획전 보고서 초안 작성해줘'를 입력해 주세요.")
    report = build_draft(run, campaign)
    existing = _prior_additions(previous)
    additions = []
    if 'naver' in intents:
        naver_note = _verified_naver_note(run, campaign)
        if not any(naver_note in x for x in _prior_additions(report)):
            additions.append('- ' + naver_note)
    if 'purchase' in intents:
        additions.append('- ' + _verified_purchase_total(run, campaign))
    if 'musinsa' in intents:
        additions.extend('- ' + line for line in _verified_musinsa_note(run))
    # 이전 보강을 보존하고 중복 줄을 제거한다.
    merged = []
    seen = set()
    for item in _prior_additions(report) + existing + additions:
        normalized = re.sub(r'^\s*-\s*', '', str(item)).strip()
        if normalized and normalized not in seen:
            merged.append('- ' + normalized)
            seen.add(normalized)
    if merged:
        # 자동 삽입된 2.1절이 있으면 교체하고, 레거시 보고서에는 추가한다.
        replacement = '\n2.1. 추가 분석 근거\n' + '\n'.join(merged)
        if '\n2.1. 추가 분석 근거\n' in report:
            report = re.sub(r'\n2\.1\. 추가 분석 근거\n.*?(?=\n\n3\. 제안 상품 구성 조건)',
                            lambda _: replacement, report, count=1, flags=re.S)
        else:
            report = report.replace('\n3. 제안 상품 구성 조건 및 콘텐츠 소구점\n',
                                    replacement + '\n\n3. 제안 상품 구성 조건 및 콘텐츠 소구점\n', 1)
    return report
