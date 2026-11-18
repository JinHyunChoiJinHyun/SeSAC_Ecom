from typing import Any

class AppError(Exception):
    """API 응답으로 바뀌는 예외의 공통 부모"""
    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "서버 오류 발생"

    def __init__(
            self,
            message: str | None = None,
            *,
            detail: dict[str, Any] | None = None
    ):
        self.message = message or self.default_message,
        self.detail = detail or {}
        super().__init__(self.message)

class InvalidRequestError(AppError):
    """요청은 올바르나 내용이 처리 불가한 요청 (예: 존재하지 않는 article_id, 스냅샷에 없는 상품 등)"""
    status_code = 422
    code = "invalid_request"
    default_message = "요청을 처리할 수 없습니다."

class NotFoundError(AppError):
    """요청한 대상이 없는 경우 (단일 리소스 조회 실패)"""
    status_code = 404
    code = "not_found"
    default_message = "대상을 찾을 수 없습니다"

class ConflictError(AppError):
    """현재 상태에서 불가한 작업"""
    status_code = 409
    code = "conflict"
    default_message = "현재 상태에서는 처리할 수 없습니다"

class AnalyticDBUpdatingError(AppError):
    """DuckDB 갱신중이라 조회 불가"""
    status_code = 503
    code = "analytics_db_updating"
    default_message = "분석 데이터 갱신 중입니다. 잠시 후 다시 시도하세요"
    retry_after_seconds = 60

class PermissionDenied(AppError):
    """권한 실패"""
    default_message = "권한이 없습니다"
    _codes = {401: "unauthorized", 403: "forbidden", 409: "stage_not_allowed"}

    def __init__(
            self,
            status_code: int,
            reason: str,
            *,
            principal=None,
            required: str | None
        ):
        if status_code not in self._codes:
            raise ValueError(f"PermissionDenied는 401/403/409만 사용: {status_code}")
        self.status_code = status_code
        self.code = self._codes[status_code]
        self.principal = principal
        self.required = required
        super().__init__(reason, detail={"required_scope": required} if required else None)

class AnalyticsLockTimeoutError(Exception):
    """batch가 DuckDB 독점 잠금을 제한 시간 내 얻지 못함. API 응답과 무관"""
        