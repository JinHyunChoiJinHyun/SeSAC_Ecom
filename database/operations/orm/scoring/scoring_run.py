"""점수 계산 실행 기록 (실패 기록은 run_event에 기록)"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    func
)

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    Mapped,
    WriteOnlyMapped,
    mapped_column,
    relationship
)

from database.operations.base import Base

if TYPE_CHECKING:
    from .target_score import TargetScore

class ScoringRun(Base):
    """기획전 대상 상품의 점수 계산 실행 이력 (성공한 실행만 기록)"""
    __tablename__ = "scoring_run"

    scoring_run_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4
    )

    # 해당 점수 계산이 수행된 워크플로 실행 ID
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run.run_id"),
        index=True
    )

    # 해당 점수 계산이 수행된 캠페인 ID
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign.campaign_id")
    )

    # 계산 당시 상품/속성 스냅샷
    target_products: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    # 재현성 및 실행 조건
    model_name: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(50))
    feature_set_version: Mapped[str] = mapped_column(String(50))
    scoring_method: Mapped[str] = mapped_column(String(50))
    scoring_config_version: Mapped[str] = mapped_column(String(50))
    feature_as_of: Mapped[date] = mapped_column(Date)

    # 대체 경로
    used_fallback: Mapped[bool]
    fallback_reason: Mapped[str | None] = mapped_column(String(30))

    # 결과 요약 (run flush 후 commit 직전에 입력)
    total_candidate_count: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # 점수 결과와의 1:N 관계 (여러 명의 점수 반환)
    target_scores: WriteOnlyMapped[TargetScore] = relationship(
        back_populates="scoring_run",
        passive_deletes=True
    )

    __table_args__ = (
        # 대체 경로를 사용했다면 대체 모델 버전과 대체 사유를 반드시 기록하고, 사용하지 않았다면 대체 사유를 기록하지 못하게 하는 무결성 제약
        CheckConstraint(
            """
            CASE
                WHEN used_fallback THEN
                    model_version LIKE 'rule\\_%'
                    AND fallback_reason IS NOT NULL
                ELSE
                    model_version NOT LIKE 'rule\\_%'
                    AND fallback_reason IS NULL
            END
            """,
            name="fallback_consistency"
        ),
        # 대체 경로 사유 제한
        CheckConstraint(
            "fallback_reason IS NULL OR fallback_reason IN ('no_active_model', 'load_failed')",
            name="fallback_reason_values"
        ),
        # 상품 스냅샷은 비어 있지 않은 JSON 배열 필요
        CheckConstraint(
            """
            CASE
                WHEN jsonb_typeof(target_products) = 'array'
                    THEN jsonb_array_length(target_products) > 0
                ELSE FALSE
            END
            """,
            name="target_products_array"
        ),
        # 결과 요약값
        CheckConstraint("total_candidate_count >= 0", name="candidate_count_nonnegative"),
        CheckConstraint("durations_ms >= 0", name="duration_nonegative"),

        # 캠페인별 최신 점수 계산 조회
        Index("ix_scoring_run_campaign_created", "campaign_id", "created_at")
    )
