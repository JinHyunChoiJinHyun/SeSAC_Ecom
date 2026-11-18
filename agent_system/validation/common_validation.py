"""공통 Validation 유틸리티.

검증 결과는 Agent마다 같은 dict 구조를 사용합니다.
성공/실패 상세 내용은 터미널 로그에만 출력하고,
Slack 최종 응답에는 검증 상세를 직접 포함하지 않는 것을 기본으로 합니다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


# 최초 실행 1회 + 재시도 2회 = 총 3회 시도
MAX_VALIDATION_RETRIES = 2


def build_validation_result(
    *,
    agent: str,
    passed: bool,
    retryable: bool,
    checks: dict[str, bool],
    errors: list[str] | None = None,
    attempt: int = 1,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """모든 Validation에서 공통으로 사용할 결과 형식을 만듭니다."""

    if max_attempts is None:
        max_attempts = MAX_VALIDATION_RETRIES + 1

    return {
        "passed": bool(passed),
        "retryable": bool(retryable),
        "agent": str(agent),
        "attempt": int(attempt),
        "max_attempts": int(max_attempts),
        "checks": dict(checks),
        "errors": list(errors or []),
    }


def log_validation_result(result: dict[str, Any]) -> None:
    """검증 결과를 터미널에만 사람이 읽기 쉬운 형태로 출력합니다."""

    agent = result.get("agent", "unknown")
    attempt = result.get("attempt", 1)
    max_attempts = result.get("max_attempts", 1)
    passed = bool(result.get("passed"))

    print(f"[{agent} Validation]")
    print(f"attempt = {attempt}/{max_attempts}")

    checks = result.get("checks") or {}
    for name, status in checks.items():
        print(f"{name} = {'PASS' if status else 'FAIL'}")

    if passed:
        print("[Validation PASS]")
        print(f"agent = {agent}")
        print(f"attempt = {attempt}")
        return

    print("[Validation FAIL]")
    for error in result.get("errors") or []:
        print(f"- {error}")

    print(f"retryable = {bool(result.get('retryable'))}")


def validate_dataframe_exists(df: Any) -> tuple[bool, str | None]:
    """입력이 pandas DataFrame인지 확인합니다."""

    if not isinstance(df, pd.DataFrame):
        return False, "결과가 pandas DataFrame이 아닙니다."

    return True, None


def validate_not_empty(df: pd.DataFrame) -> tuple[bool, str | None]:
    """DataFrame이 비어 있지 않은지 확인합니다."""

    if df.empty:
        return False, "결과 DataFrame이 비어 있습니다."

    return True, None


def validate_required_columns(
    df: pd.DataFrame,
    required_columns: set[str] | list[str] | tuple[str, ...],
) -> tuple[bool, str | None]:
    """필수 컬럼이 모두 존재하는지 확인합니다."""

    required = set(required_columns)
    missing = required - set(df.columns)

    if missing:
        return False, f"필수 컬럼이 없습니다: {sorted(missing)}"

    return True, None
