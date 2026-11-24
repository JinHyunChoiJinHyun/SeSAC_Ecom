from datetime import datetime, timezone
from typing import Callable

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.operations.orm.run.conversation_thread import ConversationThread
from database.operations.orm.run.run import Run
from database.operations.orm.run.run_event import RunEvent
from database.operations.orm.run.run_status import (
    TERMINAL_STATUSES, EVENT_TYPES, JOB_EVENT_TYPES
)

from backend.repositories.operations import  run_repository
from backend.core.exceptions import (
    NotFoundError,
    ConflictError
)

# =========================================================
# 상태 및 이벤트 상수
# =========================================================

RESUMABLE_STATUSES = (
    "waiting_approval",
    "waiting_url",
)

ALLOWED_TRANSITIONS = {
    "running": {
        "waiting_approval",
        "waiting_url",
        "scheduled",
        "done",
        "failed",
        "cancelled",
    },
    "waiting_approval": {
        "running",
        "failed",
        "cancelled",
    },
    "waiting_url": {
        "running",
        "failed",
        "cancelled",
    },
    "scheduled": {
        "running",
        "failed",
        "cancelled",
    },
}

# 공통 함수

def _now() -> datetime:
    """현재 UTC 시간 반환"""
    return datetime.now(timezone.utc)


# =========================================================
# Token 생성
# =========================================================
def get_run_token_context(
        session: Session,
        run_id: UUID
) -> dict:
    """Agent 토큰 발급에 필요한 Run 정보 조회"""
    run = run_repository.get_run_by_id(
        session=session,
        run_id=run_id
    )

    if run is None:
        raise NotFoundError("존재하지 않는 Run입니다.")

    if run.status != "running":
        raise ConflictError(f"실행 중인 Run에만 토큰을 발급할 수 있습니다: {run.status}")

    return {
        "run_id": str(run.run_id),
        "workflow": run.workflow,
        "stage": run.stage,
        "campaign_id": run.campaign_id
    }


# =========================================================
# Thread / Run 생성
# =========================================================
def create_thread(
        session: Session,
        user_id: str
) -> ConversationThread:
    """Thread 생성"""

    return run_repository.create_thread(
        session=session,
        user_id=user_id
    )

def create_run(
        session: Session,
        thread_id: UUID,
        workflow: str,
        campaign_id: UUID | None = None,
        parent_run_id: UUID | None = None
) -> Run:
    """기존 Thread에 새로운 Run 생성"""

    # Thread 행 잠금
    thread = run_repository.get_thread_for_update(
        session=session,
        thread_id=thread_id
    )

    if thread is None:
        raise ValueError("존재하지 않는 Thread 입니다.")

    # 기존 활성화 된 Run 확인
    if thread.active_run_id is not None:
        raise ValueError("해당 Thread에 이미 활성화 된 Run이 존재합니다.")

    # 부모 Run 검증
    if parent_run_id is not None:
        parent_run = run_repository.get_run_by_id(
            session=session,
            run_id=parent_run_id
        )

        if parent_run is None:
            raise ValueError("존재하지 않는 부모 Run입니다.")

        if parent_run.thread_id != thread_id:
            raise ValueError("부모 Run과 Thread가 일치하지 않습니다.")

    return run_repository.create_run(
        session=session,
        thread=thread,
        workflow=workflow,
        campaign_id=campaign_id,
        parent_run_id=parent_run_id,
        started_at=_now()
    )

def create_thread_and_run(
        session: Session,
        user_id: str,
        workflow: str,
        campaign_id: UUID | None = None
) -> tuple[ConversationThread, Run]:
    """Thread와 첫번째 Run을 함께 생성"""

    # Run 생성에 사용할 Thread 생성
    thread = run_repository.create_thread(
        session=session,
        user_id=user_id
    )

    # Run 생성 전 비즈니스 규칙 검증 및 Run 생성
    run = create_run(
        session=session,
        thread_id=thread.thread_id,
        workflow=workflow,
        campaign_id=campaign_id
    )

    return thread, run

# =========================================================
# Run 상태 관리
# =========================================================
def update_run_status(
        session: Session,
        run_id: UUID,
        new_status: str,
        reason: str | None = None
) -> Run:

    # Run 조회
    existing_run = run_repository.get_run_by_id(
        session=session,
        run_id=run_id
    )

    if existing_run is None:
        raise ValueError("존재하지 않는 Run입니다.")

    # 행 잠금 (순서: Thread -> Run)
    thread = run_repository.get_thread_for_update(
        session=session,
        thread_id=existing_run.thread_id
    )

    run = run_repository.get_run_for_update(
        session=session,
        run_id=run_id
    )

    if run is None:
        raise ValueError("존재하지 않는 Run입니다.")

    old_status = run.status

    # 이미 종료된 Run인 경우 상태 변경 불가
    if old_status in TERMINAL_STATUSES:
        raise ValueError("종료된 Run은 상태를 변경할 수 없습니다.")

    # 허용된 상태 전환인지 확인
    if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        raise ValueError(
            f"허용되지 않는 상태 전환입니다: "
            f"{old_status} -> {new_status}"
        )

    run.status = new_status

    # 종료 상태 처리
    if new_status in TERMINAL_STATUSES:
        run.finished_at = _now()

        if thread is not None and thread.active_run_id == run.run_id:
            # Thread의 활성화 된 Run 연결 해제
            run_repository.update_active_run(
                session=session,
                thread=thread,
                run_id=None
            )

    # 상태 변경 이벤트 기록
    run_repository.create_run_event(
        session=session,
        run_id=run_id,
        event_type="stage_transition",
        payload={
            "from_status": old_status,
            "to_status": new_status,
            "reason": reason
        }
    )

    session.flush()

    return run

# =========================================================
# RunEvent 기록
# =========================================================
def record_run_event(
        session: Session,
        run_id: UUID,
        event_type: str,
        agent: str | None = None,
        tool_name: str | None = None,
        payload: dict | None = None
) -> RunEvent:
    """RunEvent 기록"""

    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"지원하지 않는 이벤트 유형입니다: {event_type}"
        )

    run = run_repository.get_run_by_id(
        session=session,
        run_id=run_id
    )

    if run is None:
        raise ValueError("존재하지 않는 Run입니다.")

    # 종료된 Run은 예약 작업 이벤트만 허용
    if (
        run.status in TERMINAL_STATUSES
        and event_type not in JOB_EVENT_TYPES
    ):
        raise ValueError("종료된 Run에는 예약 작업 이벤트만 기록할 수 있습니다.")

    return run_repository.create_run_event(
        session=session,
        run_id=run_id,
        event_type=event_type,
        agent=agent,
        tool_name=tool_name,
        payload=payload
    )

def record_run_event_isolated(
        session_factory: Callable[[], Session],
        run_id: UUID,
        event_type: str,
        agent: str | None = None,
        tool_name: str | None = None,
        payload: dict | None = None
) -> RunEvent:
    """
    별도 Session(별도 트랜잭션)으로 RunEvent 기록

    - 원래 작업이 실패해 롤백되더라도 실패 기록(job_failed 등)은 남기기 위해 사용
    - 반드시 원래 작업의 트랜잭션이 끝난 뒤(rollback/commit 이후)에 호출
      (트랜잭션이 열린 상태에서 호출하면 Run 행 잠금 대기로 멈출 수 있음)
    - 정상 흐름의 이벤트는 record_run_event를 같은 Session에서 사용
    """
    with session_factory() as session:
        with session.begin():
            event = record_run_event(
                session=session,
                run_id=run_id,
                event_type=event_type,
                agent=agent,
                tool_name=tool_name,
                payload=payload
            )

        return event

# =========================================================
# Run 조회
# =========================================================
def get_run_by_campaign(
        session: Session,
        campaign_id: UUID
) -> Run | None:
    """ Campaign에 연결된 재개 가능한 Run 조회 (승인 대기 / URL 등록 대기)"""

    return run_repository.get_run_by_campaign(
        session=session,
        campaign_id=campaign_id,
        statuses=RESUMABLE_STATUSES
    )

# =========================================================
# Run 재개
# =========================================================
def claim_run_for_resume(
        session: Session,
        campaign_id: UUID
) -> Run | None:
    """재개 가능한 Run을 찾아 실행 상태로 변경"""
    # 재개 후보 조회
    candidate = get_run_by_campaign(
        session=session,
        campaign_id=campaign_id
    )

    if candidate is None:
        return None

    # 행 잠금 (순서: Thread -> Run)    
    thread = run_repository.get_thread_for_update(
        session=session,
        thread_id=candidate.thread_id
    )

    if thread is None:
        return None

    run = run_repository.get_run_for_update(
        session=session,
        run_id=candidate.run_id
    )

    if run is None:
        return None

    # 잠금 획득 후 상태 재검증
    if run.status not in RESUMABLE_STATUSES:
        return None

    # 해당 Thread의 활성화 된 Run인지 확인
    if thread.active_run_id != run.run_id:
        return None

    old_status = run.status
    run.status = "running"

    # 변경 이력 저장
    run_repository.create_run_event(
        session=session,
        run_id=run.run_id,
        event_type="stage_transition",
        payload={
            "from_status": old_status,
            "to_status": "running",
            "reason": "resume_claimed"
        }
    )

    session.flush()

    return run

    

