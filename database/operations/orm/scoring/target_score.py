"""타깃 예측 점수 저장 (scoring_run 1건당 상품 × 후보 고객별 1행)"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    String,
    UniqueConstraint
)

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.operations.base import Base

if TYPE_CHECKING:
    from .scoring_run import ScoringRun

# 4분면 세그먼트
QUADRANTS = ("core", "pref_only", "prop_only", "low") # scoring.yaml의 4분면 이름과 통일

class TargetScore(Base):
    """점수 계산 실행별 상품과 고객의 조합 점수"""

    __tablename__ = "target_score"

    scoring_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("scoring_run.scoring_run_id", ondelete="RESTRICT"),
        primary_key=True
    )
    campaign_product_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaign_product.campaign_product_id", ondelete="RESTRICT"),
        primary_key=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True
    )

    # 점수
    preference_score: Mapped[float] = mapped_column(Double) # 취향 점수
    propensity_score: Mapped[float] = mapped_column(Double) # 구매 가능성

    # 상품 내 순위 백분위와 최종 점수
    preference_pct: Mapped[float] = mapped_column(Double)
    propensity_pct: Mapped[float] = mapped_column(Double)
    final_score: Mapped[float] = mapped_column(Double) # 두 백분위의 평균으로 최종 점수 계산

    quadrant: Mapped[str] = mapped_column(String(30))
    target_rank_in_product: Mapped[int] # 상품별 고객 최종 점수로 매긴 순위

    # 근거 (예: [{"type": "preference", "feature": "...", "lift": 2.3}, ...])
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    # 점수 계산에 사용한 모델 추적 (1:N 관계)
    scoing_run: Mapped[ScoringRun] = relationship(back_populates="target_scores")

    __table_args__ = (
        CheckConstraint("preferencew_pct BETWEEN 0 AND 1", name="preference_pct_range"),
        CheckConstraint("propensity_pct BETWEEN 0 AND 1", name="propensity_pct_range"),
        CheckConstraint("final_score BETWEEN 0 AND 1", name="final_score_range"),
        CheckConstraint("target_rank_in_product >= 1", name="rank_positive"),
        CheckConstraint(
            f"quadrant IN ({', '.join(repr(q) for q in QUADRANTS)})", name="quadrant_values"
        ),
        CheckConstraint(
            """
            CASE
                WHEN jsonb_typeof(reasons) = 'array'
                THEN jsonb_array_length(reasons) BETWEEN 1 AND 3
                ELSE FALSE
            END
            """,
            name="reasons_shape"
        ),

        # 동일한 실행의 상품 내에서 순위 중복 방지 + 상위 N명 조회를 위한 인덱스 역할
        UniqueConstraint("scoring_run_id", "campaign_product_id", "target_rank_in_product", name="product_rank"),
        Index("ix_target_score_run_customer", "scoring_run_id", "customer_id")
    )