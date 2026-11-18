from datetime import date
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    String
)

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.operations.base import Base, TimestampMixin

SOURCE_TYPES = ("hm_product", "hm_sales", "external_review", "trend", "youtube", "fashion_media", "pat_campaign")

class AnalysisSnapshot(TimestampMixin, Base):
    __tablename__ = "analysis_snapshot"

    __table_args__ = (
        CheckConstraint(
            f"source_type IN {SOURCE_TYPES}",
            name="source_type_allowed"
        ),
        Index("ix_analysis_snapshot_run_id", "run_id"),
        {"schema": "evidence"}
    )

    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, 
        Identity(), 
        primary_key=True
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("run.run_id"),
        nullable=False
    )

    tool_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    analysis_date: Mapped[date] = mapped_column(
        Date,
        nullable=False
    )