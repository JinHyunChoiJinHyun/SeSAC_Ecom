from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    String,
    DateTime,
    func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uuid import UUID, uuid4
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from database.operations.base import Base

class ConversationThread(Base):
    """사용자와 에이전트 간 대화 세션 관리
    - Thread = 대화와 실행 맥락을 관리하는 공간 (모텔 객실)
    - Campaign(업무)와는 벌개의 식별 단위
    - 하나의 Campaign에 여러 Thread가 연결될 수 있음 (이론상)
    - 동일 Campaign ID를 가진 서로 다른 Thread에서 별도의 대화 및 작업 진행 가능 (이론상)
    """

    __tablename__ = "conversation_thread"

    # 기본 키
    thread_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4
    )

    # 사용자 식별자
    user_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True
    )

    # 현재 활성화 된 Run의 ID (Thread당 최대 1개)
    active_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "run.run_id",
            ondelete="SET NULL", # 참조하던 Run 삭제 시 null로 변경
            use_alter=True, # 순환 참조 해결을 위해 FK 제약조건을 별도로 추가
            name="fk_conversation_thread_active_run"
        ),
        nullable=True
    )

    # Thread 생성 시간
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Thread에 속한 Run 목록
    runs: Mapped[list["Run"]] = relationship(
        "Run",
        back_populates="thread",
        foreign_keys="Run.thread_id",
        cascade="all, delete-orphan", # 분리된 자식 객체 삭제
        passive_deletes=True # 자식 객체의 삭제 처리를 DB의 ON DELETE 규칙에 위임
    )