"""Data Agent 결과 Validation Tool."""

from __future__ import annotations

import pandas as pd

from .common_validation import (
    build_validation_result,
    validate_dataframe_exists,
    validate_not_empty,
    validate_required_columns,
)


def validate_data_result(
    df: pd.DataFrame,
    *,
    required_columns: set[str] | list[str] | None = None,
    allow_empty: bool = False,
    attempt: int = 1,
    max_attempts: int = 3,
) -> dict:
    """Data Agent가 반환한 DataFrame의 최소 실행 결과를 검증합니다."""

    checks = {
        "dataframe_exists": False,
        "not_empty": allow_empty,
        "required_columns": required_columns is None,
    }
    errors: list[str] = []

    ok, error = validate_dataframe_exists(df)
    checks["dataframe_exists"] = ok
    if not ok:
        errors.append(error or "DataFrame 검증 실패")
        return build_validation_result(
            agent="data",
            passed=False,
            retryable=True,
            checks=checks,
            errors=errors,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    if not allow_empty:
        ok, error = validate_not_empty(df)
        checks["not_empty"] = ok
        if not ok:
            errors.append(error or "빈 결과")

    if required_columns:
        ok, error = validate_required_columns(df, required_columns)
        checks["required_columns"] = ok
        if not ok:
            errors.append(error or "필수 컬럼 검증 실패")

    return build_validation_result(
        agent="data",
        passed=not errors,
        retryable=bool(errors),
        checks=checks,
        errors=errors,
        attempt=attempt,
        max_attempts=max_attempts,
    )
