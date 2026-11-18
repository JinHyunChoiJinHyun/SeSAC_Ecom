"""③ Targeting Agent Tool 4개 (「CART_SIGNAL 에이전트 및 Tool 설계안」 이름·기능 그대로).

    calculate_target_scores       고객별·상품별 구매 가능성 점수 계산
    recommend_customer_products   고객별 가장 적합한 추천 상품 지정
    segment_customers             핵심 타깃·재활성화·교차 판매·제외 그룹 구성
    build_campaign_audience       연락 가능 여부, 발송 상한, 대조군을 반영한 최종 명단 생성

설계안: 점수 계산(1)과 추천 지정(2)은 결과·책임이 달라 Tool을 나눕니다. (내부 구현은 백엔드에서 합쳐도 됨)

백엔드 API (아키텍처 [3], scope targeting:compute · audience:write)
    POST /targeting/scores            ml.inference: DuckDB 피처 → PostgreSQL scoring_run·target_score
    POST /targeting/recommendations   scoring_run 기준 고객별 최고 상품
    POST /targeting/segments          segmentation (4분면)
    POST /campaigns/{id}/audience     campaign_service + contact_policy_service → audience_version·audience_member
로컬 모드: 미구현 (고객 피처·예측 모델은 팀원 담당, 백엔드 연결 전). 규칙 점수 등 대체 결과를 만들지 않습니다.
"""

from __future__ import annotations

from agent_system import backend_client
from agent_system.tools.common import backend_or_unimplemented, clean_id, missing_id

SOURCE = "타깃 점수·명단 (예측 모델·백엔드)"
REASON = "고객 피처·구매 예측 모델(팀원 담당)과 백엔드 /targeting API 연결 전"


def calculate_target_scores(campaign_id: str, article_ids: list[str], as_of: str | None = None) -> dict:
    """고객별·상품별 구매 가능성 점수 계산. article_ids: 기획전 상품, as_of: 기준일(없으면 백엔드 ANALYSIS_AS_OF)."""
    return backend_or_unimplemented("POST", "/targeting/scores", source=SOURCE, reason=REASON, body={
        "campaign_id": clean_id(campaign_id), "article_ids": list(article_ids or []), "as_of": as_of})


def recommend_customer_products(campaign_id: str, scoring_run_id: str | None = None, top_n: int = 1) -> dict:
    """고객별 가장 적합한 추천 상품 지정 (calculate_target_scores의 scoring_run 기준)."""
    return backend_or_unimplemented("POST", "/targeting/recommendations", source=SOURCE, reason=REASON, body={
        "campaign_id": clean_id(campaign_id), "scoring_run_id": scoring_run_id, "top_n": top_n})


def segment_customers(campaign_id: str, scoring_run_id: str | None = None) -> dict:
    """핵심 타깃·재활성화·교차 판매·제외 그룹 구성 (점수 4분면)."""
    return backend_or_unimplemented("POST", "/targeting/segments", source=SOURCE, reason=REASON, body={
        "campaign_id": clean_id(campaign_id), "scoring_run_id": scoring_run_id})


def build_campaign_audience(campaign_id: str, scoring_run_id: str | None = None, control_ratio: float = 0.1,
                            max_messages_per_customer: int | None = None) -> dict:
    """연락 가능 여부(Active=1), 발송 상한, 대조군(기본 핵심 타깃 10%)을 반영한 최종 명단 생성."""
    campaign_id = clean_id(campaign_id)
    if not campaign_id and (blocked := missing_id(SOURCE, "campaign_id")):
        return blocked
    return backend_or_unimplemented("POST", f"/campaigns/{campaign_id}/audience", source=SOURCE, reason=REASON,
                                    body={"scoring_run_id": scoring_run_id, "control_ratio": control_ratio,
                                          "max_messages_per_customer": max_messages_per_customer})


TOOLS = {
    "calculate_target_scores": calculate_target_scores,
    "recommend_customer_products": recommend_customer_products,
    "segment_customers": segment_customers,
    "build_campaign_audience": build_campaign_audience,
}
TOOL_SOURCE = {name: "targeting" for name in TOOLS}


def data_status() -> dict:
    return {"targeting": "백엔드 API로 조회" if backend_client.enabled() else f"미구현 ({REASON})"}
