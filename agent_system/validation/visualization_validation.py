"""Visualization Agent 결과 Validation Tool."""

from __future__ import annotations

from pathlib import Path

from .common_validation import build_validation_result


def validate_visualization_result(
    path: str | Path | None,
    *,
    allowed_suffixes: set[str] | None = None,
    attempt: int = 1,
    max_attempts: int = 3,
) -> dict:
    """시각화 파일이 실제로 생성되었고 비어 있지 않은지 확인합니다."""

    allowed_suffixes = allowed_suffixes or {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    file_path = Path(path) if path else None

    exists = bool(file_path and file_path.exists() and file_path.is_file())
    non_empty = bool(exists and file_path.stat().st_size > 0)
    suffix_ok = bool(file_path and file_path.suffix.lower() in allowed_suffixes)

    checks = {
        "file_exists": exists,
        "file_non_empty": non_empty,
        "file_type": suffix_ok,
    }
    errors: list[str] = []

    if not exists:
        errors.append("시각화 파일이 생성되지 않았습니다.")
    if exists and not non_empty:
        errors.append("시각화 파일 크기가 0 byte입니다.")
    if file_path and not suffix_ok:
        errors.append(f"지원하지 않는 시각화 파일 형식입니다: {file_path.suffix}")

    return build_validation_result(
        agent="visualization",
        passed=not errors,
        retryable=bool(errors),
        checks=checks,
        errors=errors,
        attempt=attempt,
        max_attempts=max_attempts,
    )
