# ---------------------------------------------------------
# Run 상태
# ---------------------------------------------------------

# Thread당 하나만 허용되는 상태
ACTIVE_STATUSES = (
    "running", 
    "waiting_approval", 
    "waiting_url", 
    "scheduled"
)

TERMINAL_STATUSES = (
    "done", 
    "failed", 
    "cancelled"
)

ALL_STATUSES = ACTIVE_STATUSES + TERMINAL_STATUSES

# ---------------------------------------------------------
# RunEvent 유형
# ---------------------------------------------------------

WORKFLOW_EVENT_TYPES = (
    "agent_call",
    "tool_call",
    "user_input",
    "user_decision",
    "stage_transition",
    "permission_denied",
    "error",
)

# 종료된 Run에도 기록할 수 있는 예약 작업 이벤트
JOB_EVENT_TYPES = (
    "job_started",
    "job_completed",
    "job_failed",
    "job_blocked",
    "job_skipped",
    "job_retry_scheduled",
)

EVENT_TYPES = WORKFLOW_EVENT_TYPES + JOB_EVENT_TYPES

# ---------------------------------------------------------
# SQL 헬퍼
# ---------------------------------------------------------

def format_in_values(values: tuple[str, ...]) -> str:
    """튜플을 SQL IN 절에 사용할 문자열로 변환"""
    return ", ".join(f"'{value}'" for value in values)