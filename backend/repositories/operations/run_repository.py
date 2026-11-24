from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime

from uuid import UUID

from database.operations.orm.run.conversation_thread import ConversationThread
from database.operations.orm.run.run import Run
from database.operations.orm.run.run_event import RunEvent

# =========================================================
# Thread
# =========================================================

def create_thread(
        session: Session,
        user_id: str
) -> ConversationThread:
    "Thread 생성 (모텔 객실)"

    thread = ConversationThread(user_id=user_id)

    session.add(thread)
    session.flush() # 하위 모델 생성 후 commit 예정

    return thread

def get_thread_by_id(
        session: Session,
        thread_id: UUID
) -> ConversationThread | None:
    """Thread 조회"""

    return session.execute(
        select(ConversationThread)
        .where(ConversationThread.thread_id == thread_id)
    ).scalar_one_or_none()

def get_thread_for_update(
        session: Session,
        thread_id: UUID
) -> ConversationThread | None:
    """업데이트 전 Thread 조회 및 행 잠금 (동시 수정 방지)"""

    return session.execute(
        select(ConversationThread)
        .where(ConversationThread.thread_id == thread_id)
        .with_for_update() # 조회한 행 잠금
        .execution_options(populate_existing=True) # 세션에 해당 객체가 있더라도 조회한 최신 값으로 대체
    ).scalar_one_or_none()

def update_active_run(
        session: Session,
        thread: ConversationThread,
        run_id: UUID | None
) -> None:
    """Thread의 활성화 된 Run ID 갱신"""
    thread.active_run_id = run_id
    session.flush()

# =========================================================
# Run
# =========================================================

def create_run(
        session: Session,
        thread: ConversationThread,
        workflow: str,
        campaign_id: UUID | None = None,
        parent_run_id: int | None = None,
        started_at: datetime | None = None
) -> Run:
    """Run 생성 (업무(Campaign)를 수행하기 위한 워크플로 실행 단위)"""

    run = Run(
        thread_id = thread.thread_id,
        campaign_id=campaign_id,
        parent_run_id=parent_run_id,
        workflow=workflow,
        status="running",
        started_at=started_at
    )

    session.add()
    session.flush()

    # Thread의 활성 Run 연결 (update_active_run() 사용도 가능하나, 여기서는 직접 할당)
    thread.active_run_id = run.run_id
    session.flush()

    return run

def get_run_by_id(
        session: Session,
        run_id: UUID
) -> Run | None:
    """Run 조회"""

    return session.execute(
        select(Run)
        .where(Run.run_id == run_id)
    ). scalar_one_or_none()

def get_run_for_update(
        session: Session,
        run_id: UUID
) -> Run | None:
    """업데이트 전 Run 조회 후 행 잠금 (동시 수정 방지)"""

    return session.execute(
        select(Run)
        .where(Run.run_id == run_id)
        .with_for_update() # 기본 FOR UPDATE 잠금 사용 (FK 참조 대기 확인 후 필요 시 FOR NO KEY UPDATE 검토)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()

def get_run_by_campaign(
        session: Session,
        campaign_id: UUID,
        statuses: tuple[str, ...]
) -> Run | None:
    """Campaign에 연결된 특정 상태의 Run 조회 (워크플로 재개를 위해 대기 중인 Run 조회)"""

    return session.execute(
        select(Run)
        .where(
            Run.campaign_id == campaign_id,
            Run.status.in_(statuses)
        )
        .order_by(Run.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()

# =========================================================
# RunEvent
# =========================================================
def create_run_event(
        session: Session,
        run_id: UUID,
        event_type: str,
        agent: str | None = None,
        tool_name: str | None = None,
        payload: dict | None = None
) -> RunEvent:
    """RunEvent 생성"""

    event = RunEvent(
        run_id=run_id,
        event_type=event_type,
        agent=agent,
        tool_name=tool_name,
        payload=payload or {}
    )

    session.add(event)
    session.flush()

    return event