"""Prep Agent 결과 Validation Tool.

모델 전용 Feature/인코딩 규칙은 여기에서 고정하지 않습니다.
Prep Validation은 공통 전처리 결과만 검사합니다.
"""

from __future__ import annotations

import pandas as pd

from .common_validation import (
    build_validation_result,
    validate_dataframe_exists,
    validate_not_empty,
    validate_required_columns,
)


def validate_prep_result(
    df: pd.DataFrame,
    *,
    expected_columns: set[str] | list[str] | None = None,
    require_no_duplicates: bool = False,
    required_non_null_columns: set[str] | list[str] | None = None,
    attempt: int = 1,
    max_attempts: int = 3,
) -> dict:
    """공통 전처리 결과의 컬럼/중복/필수 결측을 검증합니다."""

    checks = {
        "dataframe_exists": False,
        "not_empty": False,
        "expected_columns": expected_columns is None,
        "duplicates": not require_no_duplicates,
        "required_non_null": required_non_null_columns is None,
    }
    errors: list[str] = []

    ok, error = validate_dataframe_exists(df)
    checks["dataframe_exists"] = ok
    if not ok:
        errors.append(error or "DataFrame 검증 실패")
        return build_validation_result(
            agent="prep",
            passed=False,
            retryable=True,
            checks=checks,
            errors=errors,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    ok, error = validate_not_empty(df)
    checks["not_empty"] = ok
    if not ok:
        errors.append(error or "빈 결과")

    if expected_columns:
        ok, error = validate_required_columns(df, expected_columns)
        checks["expected_columns"] = ok
        if not ok:
            errors.append(error or "예상 컬럼 검증 실패")

    if require_no_duplicates:
        duplicate_ok = not bool(df.duplicated().any())
        checks["duplicates"] = duplicate_ok
        if not duplicate_ok:
            errors.append("전처리 결과에 중복 행이 남아 있습니다.")

    if required_non_null_columns:
        existing = [c for c in required_non_null_columns if c in df.columns]
        missing_columns = set(required_non_null_columns) - set(existing)
        non_null_ok = not missing_columns and not bool(df[existing].isna().any().any())
        checks["required_non_null"] = non_null_ok
        if not non_null_ok:
            errors.append("필수 컬럼에 결측값이 남아 있거나 컬럼이 없습니다.")

    return build_validation_result(
        agent="prep",
        passed=not errors,
        retryable=bool(errors),
        checks=checks,
        errors=errors,
        attempt=attempt,
        max_attempts=max_attempts,
    )
