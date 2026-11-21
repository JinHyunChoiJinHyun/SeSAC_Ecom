"""H&M 일반 분석 SQL 조회용 선택형 백엔드 어댑터.

기본값은 비활성화. API 명세 확정 전까지 임시 계약을 사용합니다.
POST /analysis/query
요청: {"sql": "..."}
응답: {"data": {"rows": [{...}, ...]}}

인증 실패·잘못된 응답은 로컬로 우회하지 않습니다.
연결 실패나 503일 때만 로컬 대체 조회를 허용합니다.
"""

import os

import pandas as pd

from agent_system import backend_client


def enabled() -> bool:
    flag = os.getenv("CART_SIGNAL_HM_SQL_API_ENABLED", "").strip().lower()
    return flag in ("1", "true", "yes", "on") and backend_client.enabled()


def query(sql: str) -> tuple[pd.DataFrame | None, str | None, bool]:
    """반환: (DataFrame, 오류, 로컬 대체 조회 허용 여부)."""

    path = os.getenv(
        "CART_SIGNAL_HM_SQL_API_PATH", "/analysis/query"
    ).strip()

    if not path.startswith("/") or path.startswith("//"):
        return None, "H&M API 경로 설정 오류", False

    result = backend_client.call(
        "POST",
        path,
        body={"sql": sql},
        source="H&M",
    )

    if not result.get("available"):
        status = str(result.get("status", "백엔드 조회 오류"))
        fallback = (
            status.startswith("백엔드 호출 실패 (")
            or "HTTP 503" in status
        )
        return None, status, fallback

    data = result.get("data")
    if isinstance(data, list):
        rows = data
        columns = None
    elif isinstance(data, dict):
        rows = data.get("rows")
        columns = data.get("columns")
    else:
        return None, "H&M API 응답 구조 오류: data 형식", False

    if not isinstance(rows, list):
        return None, "H&M API 응답 구조 오류: rows 목록 없음", False

    if not all(isinstance(row, dict) for row in rows):
        return None, "H&M API 응답 구조 오류: 행 형식 불일치", False

    if columns is not None and (
        not isinstance(columns, list)
        or not all(isinstance(col, str) for col in columns)
    ):
        return None, "H&M API 응답 구조 오류: columns 형식", False

    frame = pd.DataFrame(rows, columns=columns)
    return frame, None, False
