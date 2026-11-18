import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    CheckConstraint, # 데이터 유효성 검사 (저장 값 조건 제한)
    DateTime, # 날짜 및 시간 자료형
    Index, # 인덱스 설정
    String, # 문자열 자료형
    Text, # 긴 문자열 자료형
    BigInteger,
    func # DB 함수 호출
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.operations.base import Base
from _util import _in_clause

class RunStatus(str, Enum):
    """Workflow의 실행 상태를 Enum으로 관리"""

    PENDING = "pending" # 시작 전
    RUNNING = "running" # 실행 중
    WAITING_MARKETER_APPROVAL = "wating_marketer_approval" # 마케터 승인 대기 (Interrupt)
    WAITING_TEAM_LEAD_APPROVAL = "wating_team_lead_approval" # 팀장 승인 대기 (Interrupt)
    WAITING_PAGE_REGISTRATION = "wating_page_registration" # 마케터 페이지 등록 및 URL 입력 대기
    SCHEDULED = "scheduled" # 기획전 시작 시각으로 발송 예약
    COMPLETED = "completed" # 정상 종료
    FAILED = "failed" # 오류로 종료
    CANCELLED = "cancelled" # 사용자 중단

# 사람 입력 대기 상태
WAITING_STATUSES = {
    RunStatus.WAITING_MARKETER_APPROVAL,
    RunStatus.WAITING_TEAM_LEAD_APPROVAL,
    RunStatus.WAITING_PAGE_REGISTRATION,
}

# 종료 상태
TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED
}

class Run(Base):
    """Agent Workflow 실행 정보를 저장하는 ORM 모델
    - 사용자 요청 하나에 대한 Workflow 실행 사이클 관리
    - 수정 요청이나 반려 후 재작업 시에도 기존 run에서 이어서 처리
    """

    # 테이블명
    __tablename__ = "runs"

    # 실행 ID (= LangGraph의 thread_id)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), # 자료형을 UUID 타입으로 지정
        primary_key=True,
        default=uuid.uuid4 # 새로운 객체 생성 시 자동으로 생성
    )

    # 해당 run에서 마지막으로 기록된 이벤트 번호
    last_event_seq: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0"
    )

    # 실행을 시작한 사용자 ID
    requested_by: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    # 실행할 Workflow 이름 (예: trend_planning, customer_reactivation, photography_planning 등)
    workflow_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    # Workflow 실행 상태 (RunStatus만 허용)
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=RunStatus.PENDING.value,
        server_default=RunStatus.PENDING.value,
    )

    # 현재 Workflow 단계 (예: market_analyis, content_generation, validation 등)
    current_step: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    # 사용자 요청 관련 데이터
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True
    )

    # Workflow 실행 결과 요약
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True
    )

    # Workflow 실행 실패 사유
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    # 실행 시작 시각
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # 실행 종료 시각 (completed / failed / cancelled)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # 레코드 생성 시각
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # 레코드 수정 시각
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now()
    )

    # 테이블 제약 조건
    __table_args__ = (
        # 실행 상태 유효성 검사
        CheckConstraint(
            _in_clause("status", RunStatus),
            name="ck_runs_status"
        ),
        # 종료 시각 유효성 검사 (종료 상태 여부와 종료 사각 존재 여부가 일치하는지)
        CheckConstraint(
            "(status in ('completed', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="ck_runs_finished_at"
        ),
        # 상태별 실행 목록 조회
        Index("ix_runs_status_created_at", "status", "created_at"),
        # 사용자별 실행 목록 조회
        Index("ix_runs_requested_by_created_at", "requested_by", "created_at")
    )

    @property
    def thread_id(self) -> str:
        """LangGrpah checkpointer의 thread_id (run.id와 동일)"""
        return str(self.id)

    @property
    def is_waiting(self) -> bool:
        return self.status in {s.value for s in WAITING_STATUSES}

    @property
    def is_terminal(self) -> bool:
        return self.status in {s.value for s in TERMINAL_STATUSES}

    def __repr__(self) -> str:
        return(
            f"Run(id={self.id} ,"
            f"workflow_name={self.workflow_name}, "
            f"(status={self.status})"
        )

