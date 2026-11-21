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


def build_draft(run, campaign):
    """확정 정보만 사용해 보고서 초안을 만든다. 타깃 수·성과·발송을 추정하지 않는다."""
    values = (campaign or {}).get("values") or {}
    if not values.get("products") or _chosen_copy(values.get("page")) == "미확정":
        raise ValueError("주력 상품과 대표 문구를 먼저 확정해야 보고서 초안을 만들 수 있습니다.")
    ids = re.findall(r"(?<!\d)\d{10}(?!\d)", str(values.get("products")))
    if not ids:
        raise ValueError("실제 H&M 상품 ID가 없어 보고서 초안을 만들 수 없습니다.")
    colors = values.get("colors") or []
    if isinstance(colors, str):
        colors = [x.strip() for x in colors.split(",") if x.strip()]
    subtype = _text(values.get("subtype"), _text(values.get("category")))
    theme = _text(values.get("theme"), f"겨울 {_text(values.get('category'))} 컬렉션")
    schedule = _text(values.get("schedule"))
    points = _selling_points(values.get("selling_points"))
    rows = {str(row.get("article_id")): row for row in run.get("verified_candidate_rows") or [] if isinstance(row, dict)}
    product_lines = []
    for num, aid in enumerate(ids, 1):
        r = rows.get(aid, {})
        name = _text(r.get("prod_name") or r.get("product_name"), "상품명 확인 필요")
        color = _text(r.get("colour_group_name") or r.get("color"), "색상 확인 필요")
        count = r.get("winter_online_purchases")
        purchases = f"과거 겨울 온라인 구매 {int(count):,}건" if isinstance(count, (int,float)) else "구매 건수 확인 필요"
        product_lines.append(f"{num}. {name} / {aid} / {color} / {purchases}")
    market = run.get("category_market") or {}
    ranked = market.get("ranked_evidence") or (run.get("campaign_summary") or {}).get("ranked_evidence") or {}
    market_note = "무신사 상위 랭킹 및 관련 리뷰: 해당 데이터의 수집 시점·표본·분류 한계를 확인해야 합니다."
    if ranked:
        market_note = (f"무신사 아우터 상위 100개 수집 표본에서 여성·공용 상품의 관련 잠정 후보 "
                       f"{ranked.get('related_products', '미확인')}개와 연결 리뷰 "
                       f"{ranked.get('related_reviews', '미확인')}건을 참고했습니다. "
                       "상품명 기반 잠정 분류이며 판매량·시장 선호도 순위가 아닙니다.")
    return (f"■ 기획전 운영 제안서 초안 (타깃 모델 연결 전)\n"
            f"\n1. 제안 요약\n기획전: {theme}\n"
            f"대분류·소분류: {_text(values.get('category'))} / {subtype}\n"
            f"선정 색상: {', '.join(colors) if colors else '미확정'}\n"
            f"기간: {schedule}\n"
            f"대표 문구: {_chosen_copy(values.get('page'))}\n"
            "\n2. 선정 상품 및 근거\n"
            + "\n".join(product_lines) + "\n"
            "선정 근거: H&M 상품명·설명 기준 세부 유형 매칭과 20~39세·여성용 상품·겨울(12~2월)·온라인 채널의 과거 거래 기록."
            " 현재 매출이나 재고는 아닙니다.\n"
            f"{market_note}\n"
            "\n3. 콘텐츠 및 소구점\n"
            + ("확정 소구점: " + ", ".join(points) if points else "확정 소구점: 확인 필요") + "\n"
            "선정 상품에서 확인된 색상·디자인 정보만 소개하며, 미검증 보온성·핏·품질·할인율은 확정적으로 주장하지 않습니다.\n"
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
            "상품·색상·대표 문구는 마케터가 확정했지만, 기간·현재 재고·가격·혜택·실제 발송 대상·마케팅 수신 동의·URL은 별도 확인이 필요합니다.\n"
            "\n[초안 상태] 타깃 모델 미연결로 실행 계획의 일부만 작성됐습니다. "
            "팀장 승인 메일 발송, 실제 고객 선정·메시지 발송, 페이지 공개 및 사후 예약은 수행하지 않았습니다. "
            "기존 워크플로는 고객 타깃 분석 진입 전 상태를 유지합니다.")
