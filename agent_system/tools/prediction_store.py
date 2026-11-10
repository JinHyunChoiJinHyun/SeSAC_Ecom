"""
Purchase Prediction Store 조회 Tool

팀 합의
-------
별도의 mart.model_predictions 테이블은 만들지 않습니다.

앞으로 예측 결과의 공통 저장소 역할은
mart.purchase_predictions 테이블이 담당합니다.

현재 작업 단계
-------------
1. Slack 요청에서는 mart.purchase_predictions를 읽기만 합니다.
2. 팀원이 purchase_predictions의 최종 컬럼 구조를 수정 중입니다.
3. 따라서 현재 persist_result=False를 유지합니다.
4. 팀원의 테이블 수정이 완료되면 최종 컬럼을 확인한 뒤
   save_prediction_dataframe()의 저장 규칙을 확정하고
   persist_result=True를 연결합니다.

중요
----
현재 이 파일의 조회 SQL은 2026-11-06 기준 확인된
purchase_predictions 컬럼 구조에 맞춰져 있습니다.

현재 확인된 컬럼:
- predicted_at
- cart_event_id
- client_id
- sku
- hours_after_cart
- cart_timestamp
- purchase_probability
- promotion_segment
- model_name

팀원이 컬럼을 변경한 뒤에는
이 파일의 조회 컬럼도 최종 Schema에 맞춰 수정해야 합니다.
"""

from __future__ import annotations

import re

import pandas as pd

from database.db_client import (
    get_duckdb_connection,
)


# =========================================================
# 공통 Prediction Store
# =========================================================

# 팀 합의:
# mart.model_predictions를 별도로 만들지 않고,
# 이 테이블이 현재/향후 예측 결과 저장소 역할을 담당합니다.
TABLE_NAME = (
    "mart.purchase_predictions"
)


# =========================================================
# 현재 Schema 상태
# =========================================================

# [중요]
# 아래 컬럼은 2026-11-06 기준 현재 DB에서 확인한 컬럼입니다.
#
# 팀원이 purchase_predictions 컬럼을 수정 중이므로
# 최종 Schema 확정 후 이 목록과 조회 SQL을 다시 맞춰야 합니다.
CURRENT_REQUIRED_COLUMNS = {
    "predicted_at",
    "cart_event_id",
    "client_id",
    "sku",
    "hours_after_cart",
    "purchase_probability",
    "promotion_segment",
    "model_name",
}


def _table_exists(
    connection,
) -> bool:
    """mart.purchase_predictions 존재 여부를 확인합니다."""

    result = (
        connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'mart'
              AND table_name = 'purchase_predictions'
            """
        )
        .fetchone()
    )

    return bool(
        result
        and int(
            result[0]
        ) > 0
    )


def _get_table_columns(
    connection,
) -> set[str]:
    """현재 purchase_predictions의 실제 컬럼명을 확인합니다."""

    rows = (
        connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'mart'
              AND table_name = 'purchase_predictions'
            ORDER BY ordinal_position
            """
        )
        .fetchall()
    )

    return {
        str(
            row[0]
        )
        for row in rows
    }


def _validate_current_read_schema(
    connection,
) -> None:
    """
    현재 조회 코드에 필요한 컬럼이 남아 있는지 확인합니다.

    팀원이 테이블 컬럼을 수정한 뒤 이 검증에서 실패하면
    prediction_store.py를 최종 Schema에 맞춰 수정해야 합니다.
    """

    actual_columns = (
        _get_table_columns(
            connection
        )
    )

    missing = (
        CURRENT_REQUIRED_COLUMNS
        - actual_columns
    )

    if missing:
        raise RuntimeError(
            "mart.purchase_predictions의 컬럼 구조가 변경되었습니다. "
            "현재 조회 코드에 필요한 컬럼이 없습니다: "
            f"{sorted(missing)}. "
            "팀원이 확정한 최종 Schema에 맞춰 "
            "prediction_store.py 조회 규칙을 수정해주세요."
        )


def _infer_prediction_hour(
    model_key: str,
    observation_window_hours: int | None,
) -> int | None:
    """
    prediction_hour가 직접 전달되면 그대로 사용하고,
    없으면 cart_purchase_24h 형태의 model_key에서 추출합니다.
    """

    if observation_window_hours is not None:
        return int(
            observation_window_hours
        )

    match = re.fullmatch(
        r"cart_purchase_(\d+)h",
        str(
            model_key
        ),
    )

    if not match:
        return None

    return int(
        match.group(
            1
        )
    )


def get_ranked_predictions(
    *,
    model_key: str,
    top_n: int = 10,
    ascending: bool = False,
    observation_window_hours: int | None = None,
    target_id: str | None = None,
) -> pd.DataFrame:
    """
    mart.purchase_predictions에서 최신 예측 결과를 직접 조회합니다.

    처리 방식
    --------
    - 최신 predicted_at 배치만 사용
    - 고객별 purchase_probability 대표값 1건 선택
    - DuckDB 안에서 정렬과 Top-N 처리
    - 필요한 소량의 행만 pandas로 가져옴

    현재는 읽기 전용입니다.
    INSERT / UPDATE / DELETE를 수행하지 않습니다.
    """

    prediction_hour = (
        _infer_prediction_hour(
            model_key=
                model_key,

            observation_window_hours=
                observation_window_hours,
        )
    )

    if prediction_hour is None:
        return pd.DataFrame()

    top_n = max(
        1,
        min(
            int(
                top_n
            ),
            1000,
        ),
    )

    order = (
        "ASC"
        if ascending
        else "DESC"
    )

    target_condition = ""
    params = [
        int(
            prediction_hour
        ),
        int(
            prediction_hour
        ),
    ]

    if target_id is not None:
        target_condition = (
            " AND CAST(sku AS VARCHAR) = ?"
        )

        params.append(
            str(
                target_id
            )
        )

    connection = (
        get_duckdb_connection(
            read_only=True
        )
    )

    try:
        if not _table_exists(
            connection
        ):
            return pd.DataFrame()

        # -------------------------------------------------
        # 팀원이 컬럼을 수정 중이므로,
        # 예상과 다른 Schema가 되면 조용히 잘못된 결과를 내지 않고
        # 명확한 오류를 발생시킵니다.
        # -------------------------------------------------
        _validate_current_read_schema(
            connection
        )

        query = f"""
        WITH latest_run AS (
            SELECT
                MAX(predicted_at) AS predicted_at
            FROM {TABLE_NAME}
            WHERE hours_after_cart = ?
        ),

        ranked AS (
            SELECT
                predicted_at,
                cart_event_id,
                client_id,
                sku,
                hours_after_cart,
                cart_timestamp,
                purchase_probability,
                promotion_segment,
                model_name,

                ROW_NUMBER() OVER (
                    PARTITION BY client_id
                    ORDER BY
                        purchase_probability {order},
                        cart_event_id DESC
                ) AS rn

            FROM {TABLE_NAME}

            WHERE hours_after_cart = ?
              AND predicted_at = (
                  SELECT predicted_at
                  FROM latest_run
              )
              {target_condition}
        )

        SELECT
            predicted_at,
            cart_event_id,
            client_id,
            sku,
            hours_after_cart,
            cart_timestamp,
            purchase_probability,
            promotion_segment,
            model_name

        FROM ranked

        WHERE rn = 1

        ORDER BY
            purchase_probability {order},
            client_id

        LIMIT {top_n}
        """

        result = (
            connection.execute(
                query,
                params,
            )
            .fetchdf()
        )

        result.attrs[
            "prediction_source"
        ] = TABLE_NAME

        return result

    finally:
        connection.close()


def save_prediction_dataframe(
    *args,
    **kwargs,
) -> int:
    """
    [현재 사용 금지]

    팀 합의에 따라 향후 저장 대상도
    mart.purchase_predictions를 사용할 예정입니다.

    하지만 현재 팀원이 이 테이블의 컬럼 구조를 수정 중이므로
    저장 규칙을 지금 확정하면 팀원의 작업과 충돌할 수 있습니다.

    따라서 현재 단계에서는:
        persist_result=False 유지
        purchase_predictions 읽기만 수행

    팀원 수정 완료 후:
        1. 최종 purchase_predictions 컬럼 확인
        2. Model Profile 결과 → 최종 컬럼 Mapping 정의
        3. 중복 방지 Key 확정
        4. 이 함수의 INSERT / UPSERT 구현
        5. persist_result=True 연결

    실수로 persist_result=True가 켜져도
    수정 중인 테이블에 데이터를 쓰지 않도록 명시적으로 차단합니다.
    """

    raise RuntimeError(
        "현재 mart.purchase_predictions는 팀원이 컬럼 구조를 수정 중이므로 "
        "예측 결과 저장을 비활성화했습니다. "
        "persist_result=False를 유지하고, "
        "최종 Schema 확정 후 저장 규칙을 구현해주세요."
    )
