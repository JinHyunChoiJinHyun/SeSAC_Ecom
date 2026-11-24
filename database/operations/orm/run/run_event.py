from datetime import datetime
from typing import Any

from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    DateTime,
    func,
    Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.operations.base import Base
from .run_status import (
    WORKFLOW_EVENT_TYPES, JOB_EVENT_TYPES, EVENT_TYPES, format_in_values
)

class RunEvent(Base):
    """Run 실행 과정에서 발생한 이벤트 이력"""

    __tablename__ = "run_event"

    # 기본 키
    event_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # 소속 Run
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "run.run_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    # 이벤트 유형
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    # 이벤트 발생 시킨 Agent
    agent: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    # 호출한 Tool 이름
    tool_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    # 이벤트 상세 데이터
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}"
    )

    # 이벤트 발생 시간
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Run 관계
    run: Mapped["Run"] = relationship(
        "Run",
        back_populates="events"
    )

    # 이벤트 유형 검증
    __table_args__ = (
        CheckConstraint(
            f"""
            event_type IN (
                {format_in_values(EVENT_TYPES)}
            )
            """,
            name="event_type"
        ),
        Index("ix_run_event_run_id_event_id", "run_id", "event_id")
    )