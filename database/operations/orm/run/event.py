import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    event,
    func,
    text
)

from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.operations.base import Base
from _util import _in_clause

class EventType(str, Enum):
    """Workflow 실행 중 발생하는 이벤트 유형"""

    # run 생명주기
    RUN_STARTED = "run_started"
    RUN_PAUSED = "run_paused" # Interrupt로 인한 대기 상태
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"

    # Wokkflow / Agent 실행 단계 (진행 사항 표시 및 실행 이력 추적)
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"

    # Tool 호출
    TOOL_CALLED = "tool_called"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"

    # 사용자 입력
    USER_INPUT_RECEIVED = "user_input_received"

    # 승인 (판단 기준은 approval 테이블)
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"

    # 발송
    DELIVERY_SCHEDULED = "delivery_scheduled"
    DELIVERY_BLOCKED = "delivery_blocked"
    DELIVERY_COMPLETED = "delivery_completed"

class EventActorType(str, Enum):
    """이벤트 발생 주체 유형"""
    USER = "user" # 마케터, 팀장 등
    AGENT = "agent" # 각 Agent
    SYSTEM = "system" # Backend, worker, 배치 등

# tool_call_id가 반드시 필요한 이벤트
TOOL_EVENT_TYPES = {
    EventType.TOOL_CALLED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED
}



class RunEvent(Base):
    """Workflow 실행 과정에서 발생한 이벤트 이력 (수정/삭제 불가)
    - 진행 상황 표시 및 실행 이력 추적에 사용

    event_type별 event_data 예시
    - run_paused          {"waiting_for": "marketer_approval" | "team_lead_approval" | "page_registration"}
    - run_resumed         {"resumed_by": "approval" | "url_registration", "ref_id": "..."}
    - step_*              {"step": "content_generation"}
    - tool_called         {"tool": "validate_marketing_copy", "args_summary": {...}}
    - tool_completed      {"tool": "...", "result_summary": {...}, "duration_ms": 820}
    - tool_failed         {"tool": "...", "error": "..."}
    - user_input_received {"input_type": "chat" | "url_registration", "summary": "..."}
    - approval_requested  {"approver_role": "team_lead", "content_hash": "..."}
    - approval_decided    {"approval_id": "...", "approver_role": "...", "decision": "..."}
    - delivery_scheduled  {"campaign_id": "...", "send_at": "2026-11-20T09:00:00+09:00"}
    - delivery_blocked    {"campaign_id": "...", "reasons": ["team_lead_approval_missing"]}
    - delivery_completed  {"campaign_id": "...", "sent_count": 1180, "excluded_count": 60}
    """

    __tablename__ = "run_events"

    # 이벤트 ID
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    # 이벤트가 발생한 Workflow ID (이력 보존을 위해 삭제 차단)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="RESTRICT"),
        nullable=False
    )

    # run 내 이벤트 순서 
    # 상위 테이블에서 발급 받아 순서 중복 방지
    # 하위 테이블에서 직접 생성하면 동시 요청 시 순번 중복 및 이벤트 기록 충돌 가능
    run_seq: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False
    )

    # 이벤트 유형
    event_type: Mapped[str] = mapped_column(
        String(60),
        nullable=False
    )

    # 동일한 Tool 실행에서 발생한 이벤트 연결하는 ID
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True
    )

    # 이벤트 발생 주체 ID (사용자 ID, Agent 이름, 시스템 이름)
    actor_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    # 이벤트 상세 데이터 (마스킹/요약된 값만 저장)
    event_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}::jsonb")
    )

    # 이벤트 발생 시각
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp()
    )

    __table_args__ = (
        # 이벤트 유형 제한
        CheckConstraint(
            _in_clause("event_type", EventType),
            name="ck_run_events_event_type"
        ),

        # 이벤트 발생 주체 유형 제한
        CheckConstraint(
            _in_clause("actor_type", EventActorType),
            name="ck_run_events_actor_type"
        ),

        # 사용자 이벤트 주체 기록 (user인 경우에만 actor_id 필수로 지정)
        CheckConstraint(
            "actor_type <> 'user' OR actor_id IS NOT NULL" , # 두가지 중 하나라도 참이면 통과
            name="ck_run_events_user_actor_id"
        ),

        # Tool 이벤트는 tool_call_id 필수 (Tool 이벤트인 경우에만 tool_call_id를 필수로 지정)
        CheckConstraint(
            "NOT (" + _in_clause("event_type", TOOL_EVENT_TYPES) + ") "
            "OR tool_call_id IS NOT NULL",
            name="ck_run_events_tool_correlation"
        ),

        # run_seq는 1 이상
        CheckConstraint(
            "run_seq >= 1",
            name="ck_run_events_run_seq_positive"
        ),

        # run 내 순번 중복 금지
        UniqueConstraint("run_id", "run_seq", name="uq_run_events_run_id_run_seq"),

        # 이벤트 유형별 조회
        Index("ix_run_events_event_type_created_at", "event_type", "created_at"),

        # 같은 Tool 호출의 이벤트 묶음 조회
        Index("ix_run_events_tool_call_id", "tool_call_id")
    )

    def __repr__(self) -> str:
        return(
            f"RunEvent(run_seq={self.run_seq}, "
            f"run_id={self.run_id}, "
            f"event_type={self.event_type})"
        )

class RunEventImmutableError(Exception):
    """수정 및 삭제 시도 시 발생"""

# ORM 수준 차단: SQLAlchemy 세션이 RunEvent 객체의 수정/삭제를 반영할 때 바로 실패 처리
# 일괄 처리 혹은 직접 쿼리 작성은 객체를 거치지 않으므로 DB 트리거가 차단
@event.listens_for(RunEvent, "before_update")
def _block_run_event_update(mapper, connection, target: RunEvent) -> None:
    raise RunEventImmutableError(
        f"run_events는 추가만 가능합니다 (UPDATE 차단, run_id={target.run_id}, run_seq={target.run_seq})"
    )

@event.listens_for(RunEvent, "before_delete")
def _block_run_event_update(mapper, connection, target: RunEvent) -> None:
    raise RunEventImmutableError(
        f"run_events는 추가만 가능합니다 (DELETE 차단, run_id={target.run_id}, run_seq={target.run_seq})"
    )