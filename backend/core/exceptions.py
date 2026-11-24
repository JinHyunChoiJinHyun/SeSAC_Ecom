"""
backend/core/exceptions.py
서비스·저장소·의존성이 던지는 예외. FastAPI를 import하지 않는다 (HTTP 응답 변환은 api/error_handlers.py).

- 서비스는 HTTPException 대신 여기의 예외를 던진다 → 서비스가 HTTP를 몰라도 되고, 워커·batch에서도 같은 예외를 쓴다
- status_code·code는 응답 변환에 쓰는 값. code는 backend_client가 기계적으로 분기할 때 쓴다 (예: 503 재시도)
"""
from typing import Any


class AppError(Exception):
    """API 응답으로 바뀌는 예외의 공통 부모"""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "서버 오류가 발생했습니다"

    def __init__(self, message: str | None = None, *, detail: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.detail = detail or {}
        super().__init__(self.message)


class InvalidRequestError(AppError):
    """형식은 맞지만 내용이 처리할 수 없는 요청 (없는 article_id, 스냅샷에 없는 상품 등)"""
    status_code = 422
    code = "invalid_request"
    default_message = "요청을 처리할 수 없습니다"


class NotFoundError(AppError):
    """요청한 대상이 없음 (스냅샷, 캠페인 등)"""
    status_code = 404
    code = "not_found"
    default_message = "대상을 찾을 수 없습니다"


class ConflictError(AppError):
    """현재 상태에서 할 수 없는 작업 (이미 확정된 명단 변경 등)"""
    status_code = 409
    code = "conflict"
    default_message = "현재 상태에서는 처리할 수 없습니다"


class AnalyticsDBUpdatingError(AppError):
    """DuckDB 갱신 중이라 조회할 수 없음 (아키텍처 [8]). backend_client가 ToolResult로 바꿔 재시도 안내"""
    status_code = 503
    code = "analytics_db_updating"
    default_message = "분석 데이터 갱신 중입니다. 잠시 후 다시 시도하세요"
    retry_after_seconds = 60


class PermissionDenied(AppError):
    """권한 실패 (아키텍처 [4] 검사 1~5단계). 예외 핸들러가 run_event(permission_denied)로 기록한다"""
    default_message = "권한이 없습니다"
    _codes = {401: "unauthorized", 403: "forbidden", 409: "stage_not_allowed"}

    def __init__(self, status_code: int, reason: str, *, principal=None, required: str | None = None):
        if status_code not in self._codes:
            raise ValueError(f"PermissionDenied는 401/403/409만 씁니다: {status_code}")
        self.status_code = status_code
        self.code = self._codes[status_code]
        self.principal = principal
        self.required = required
        super().__init__(reason, detail={"required_scope": required} if required else None)


class AnalyticsLockTimeoutError(Exception):
    """batch가 DuckDB 독점 잠금을 제한 시간 안에 얻지 못함. API 응답과 무관해서 AppError가 아니다"""

    