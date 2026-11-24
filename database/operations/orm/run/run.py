from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    DateTime,
    func,
    text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.operations.base import Base
from .run_status import(
    ACTIVE_STATUSES, TERMINAL_STATUSES, ALL_STATUSES, format_in_values
)

class Run(Base):
    """에이전트들이 협업하여 하나의 업무(workflow)를 수행하는 실행 단위"""
    __tablename__ = "run"

    # 기본 키 (손님이 업무를 위해 수행하는 작업(워크 플로우) 1회 실행 단위)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4
    )

    # 소속 Thread 식별 (객실)
    thread_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "conversation_thread.thread_id",
            ondelete="CASCADE"
        ),
        nullable=False,
        index=True
    )

    # 업무 단위 식별 ID (객실 손님이 수행하는 업무 단위)
    campaign_id: Mapped[int | None] = mapped_column(
        PG_UUID(as_uuid=True),
        # ForeignKey(
        #     "run.run_id",
        #     ondelete="SET NULL"
        # ),
        nullable=True,
        index=True
    )

    # 부모 Run
    parent_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "run.run_id",
            ondelete="SET NULL"
        ),
        nullable=True,
        index=True
    )

    # 실행 workflow 이름
    workflow: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    # 실행 상태
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="running",
        server_default=text("'running'")
    )

    stage: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    # 실행 시작 시간
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # 실행 종료 시간
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Thread 관계
    thread: Mapped["ConversationThread"] = relationship(
        "ConversationThread",
        back_populates="runs",
        foreign_keys=[thread_id]
    )

    # 부모 Run 관계
    parent: Mapped["Run | None"] = relationship(
        "Run",
        back_populates="children",
        remote_side=[run_id],
        foreign_keys=[parent_run_id]
    )

    # 자식 Run 목록
    children: Mapped[list["Run"]] = relationship(
        "Run",
        back_populates="parent",
        foreign_keys=[parent_run_id]
    )

    # RunEvent 목록
    events: Mapped[list["RunEvent"]] = relationship(
        "RunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # 테이블 제약 조건 및 인덱스
    __table_args__ = (
        CheckConstraint(
            f"""
            status IN (
                {format_in_values(ALL_STATUSES)}
            )
            """,
            name="status"
        ),

        CheckConstraint(
            f"(status IN ({format_in_values(TERMINAL_STATUSES)})) = (finished_at IS NOT NULL)",
            name="cfinished_at"
        ),

        # 각 Thread별로 활성 상태인 Run은 최대 1개만 허용
        Index(
            "uq_run_active_thread",
            "thread_id",
            unique=True,
            postgresql_where=text(
                f"""
                status IN (
                    {format_in_values(ACTIVE_STATUSES)}
                )
                """
            )
        ),

        # Campaign별 재개 가능한 Run 조회
        Index(
            "ix_run_campaign_status_started",
            "campaign_id",
            "status",
            "started_at"
        )
    )

