"""
Prep Agent 전처리 함수 모듈

등록된 데이터 출처(common/data_sources.py)의 컬럼 설정을 반영한 기본 전처리 함수들을 정의합니다.

중요 원칙
---------
1. 원본 DB는 직접 수정하지 않습니다.
2. 입력 DataFrame을 copy()한 뒤 처리합니다.
3. 중복은 자동 삭제하지 않습니다.
   (H&M 구매 기록의 같은 날·같은 고객·같은 상품 반복은 수량으로 봅니다)
4. 결측치는 무조건 중앙값으로 채우지 않습니다.
5. 날짜 컬럼은 datetime으로 변환합니다.
   후보 컬럼은 출처별 timestamp_columns 설정을 따릅니다 (예: H&M t_dat, Amazon timestamp).
   epoch 정수는 값의 크기로 초/밀리초를 판단합니다.
6. 반복 기록은 삭제 대신 event_repeat_count Feature로 보존할 수 있습니다.
"""

import numpy as np
import pandas as pd

from common.data_sources import merged


# 출처와 관계없이 흔히 쓰는 날짜 컬럼 이름
_GENERIC_TIMESTAMP_COLUMNS = [
    "date",
    "collected_at",
    "created_at",
    "published_at",
    "review_date",
    "month",
    "week",
]

# 날짜/시간 컬럼 후보 (앞에 있을수록 우선): 출처별 설정 + 일반 이름
TIMESTAMP_COLUMN_CANDIDATES = list(
    dict.fromkeys([*merged("timestamp_columns"), *_GENERIC_TIMESTAMP_COLUMNS])
)

# 개체(고객·작성자·상품) 컬럼 후보: 출처별 설정
ENTITY_COLUMN_CANDIDATES = list(merged("entity_columns"))

# 반복 기록 기본 그룹 조합: 출처별 설정
REPEAT_GROUP_PRESETS = [list(preset) for preset in merged("repeat_group_presets")]


def detect_timestamp_column(df: pd.DataFrame) -> str:
    """DataFrame에서 날짜/시간 컬럼 이름을 찾습니다. 없으면 빈 문자열."""

    if df is None:
        return ""

    for column in TIMESTAMP_COLUMN_CANDIDATES:
        if column in df.columns:
            return column

    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            return column

    return ""


def detect_entity_column(df: pd.DataFrame) -> str:
    """DataFrame에서 정렬·반복 집계 기준이 될 개체 컬럼을 찾습니다. 없으면 빈 문자열."""

    if df is None:
        return ""

    for column in ENTITY_COLUMN_CANDIDATES:
        if column in df.columns:
            return column

    return ""


def default_repeat_group_columns(df: pd.DataFrame) -> list[str]:
    """반복 기록 Feature의 기본 그룹 기준을 정합니다.

    - 출처별 repeat_group_presets 중 DataFrame에 모두 있는 첫 조합
      (예: H&M 구매 기록 t_dat + customer_id + article_id = 같은 날 같은 상품 수량)
    - 그 밖: 찾을 수 있는 개체 + 시간 컬럼
    """

    if df is None:
        return []

    for preset in REPEAT_GROUP_PRESETS:
        if all(column in df.columns for column in preset):
            return preset

    return [
        column
        for column in [detect_entity_column(df), detect_timestamp_column(df)]
        if column
    ]


def check_dataframe_info(df: pd.DataFrame) -> dict:
    """DataFrame의 행/열 수, 컬럼, dtype을 확인합니다."""

    if df is None:
        return {
            "row_count": 0,
            "column_count": 0,
            "columns": [],
            "dtypes": {},
            "error": "DataFrame이 None입니다.",
        }

    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "dtypes": {
            column: str(dtype)
            for column, dtype in df.dtypes.items()
        },
        "error": None,
    }


def check_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼별 결측치 개수와 비율을 확인합니다."""

    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "column_name",
                "null_count",
                "null_ratio",
            ]
        )

    total_rows = len(df)

    result = []

    for column in df.columns:
        null_count = int(df[column].isna().sum())

        null_ratio = (
            null_count / total_rows
            if total_rows > 0
            else 0.0
        )

        result.append(
            {
                "column_name": column,
                "null_count": null_count,
                "null_ratio": null_ratio,
            }
        )

    return pd.DataFrame(result)


def check_duplicates(df: pd.DataFrame) -> dict:
    """
    완전히 동일한 행의 중복 여부를 확인합니다.

    구매 기록의 반복 행은 수량일 수 있으므로 (예: H&M)
    자동 삭제하지 않습니다.
    """

    if df is None or df.empty:
        return {
            "row_count": 0,
            "duplicate_rows": 0,
            "duplicate_ratio": 0.0,
            "unique_rows": 0,
        }

    row_count = len(df)
    duplicate_rows = int(df.duplicated().sum())
    unique_rows = int(row_count - duplicate_rows)

    duplicate_ratio = (
        duplicate_rows / row_count
        if row_count > 0
        else 0.0
    )

    return {
        "row_count": row_count,
        "duplicate_rows": duplicate_rows,
        "duplicate_ratio": duplicate_ratio,
        "unique_rows": unique_rows,
    }


def convert_timestamp(
    df: pd.DataFrame,
    timestamp_column: str = ""
):
    """날짜/시간 컬럼을 pandas datetime으로 변환합니다.

    timestamp_column을 비우면 t_dat, timestamp 순서로 자동 선택합니다.
    """

    if not timestamp_column:
        timestamp_column = detect_timestamp_column(df) or "timestamp"

    if df is None:
        return None, {
            "success": False,
            "error": "DataFrame이 None입니다.",
        }

    processed_df = df.copy()

    if timestamp_column not in processed_df.columns:
        return processed_df, {
            "success": False,
            "error": f"'{timestamp_column}' 컬럼이 없습니다.",
        }

    before_null_count = int(
        processed_df[timestamp_column]
        .isna()
        .sum()
    )

    series = processed_df[timestamp_column]

    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        # Unix epoch 초·밀리초 범위에 해당하는 값만 날짜로 변환
        numeric = pd.to_numeric(series, errors="coerce")
        valid = numeric.dropna()

        if valid.empty:
            return processed_df, {
                "success": False,
                "error": "변환 가능한 숫자 날짜 값이 없습니다.",
            }

        # 지원 범위: 1971-01-01부터 2100-01-01 미만
        min_seconds = 31536000
        max_seconds = 4102444800

        seconds_ok = valid.between(
            min_seconds, max_seconds, inclusive="left"
        ).all()

        millis_ok = valid.between(
            min_seconds * 1000,
            max_seconds * 1000,
            inclusive="left",
        ).all()

        if seconds_ok:
            unit = "s"
        elif millis_ok:
            unit = "ms"
        else:
            return processed_df, {
                "success": False,
                "error": (
                    "숫자 값이 Unix epoch 초/밀리초 범위에 있지 않습니다. "
                    "month, week, YYYYMM 등의 정수는 날짜로 자동 변환하지 않습니다."
                ),
            }

        converted = pd.to_datetime(
            numeric,
            unit=unit,
            errors="coerce",
        )
    else:
        converted = pd.to_datetime(
            series,
            errors="coerce"
        )

    after_null_count = int(
        converted
        .isna()
        .sum()
    )

    processed_df[timestamp_column] = converted

    return processed_df, {
        "success": True,
        "timestamp_column": timestamp_column,
        "before_null_count": before_null_count,
        "after_null_count": after_null_count,
        "new_parse_failures": (
            after_null_count
            - before_null_count
        ),
    }


def sort_by_timestamp(
    df: pd.DataFrame,
    client_column: str = "",
    timestamp_column: str = ""
):
    """개체(고객·작성자·상품)별 시간 순서로 정렬합니다.

    컬럼을 비우면 등록된 출처의 컬럼 후보로 자동 선택합니다.
    """

    if not client_column:
        client_column = detect_entity_column(df) or "customer_id"
    if not timestamp_column:
        timestamp_column = detect_timestamp_column(df) or "t_dat"

    if df is None:
        return None, {
            "success": False,
            "error": "DataFrame이 None입니다.",
        }

    processed_df = df.copy()

    required_columns = [
        client_column,
        timestamp_column,
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in processed_df.columns
    ]

    if missing_columns:
        return processed_df, {
            "success": False,
            "error": (
                "정렬에 필요한 컬럼이 없습니다: "
                f"{missing_columns}"
            ),
        }

    if not pd.api.types.is_datetime64_any_dtype(
        processed_df[timestamp_column]
    ):
        return processed_df, {
            "success": False,
            "error": (
                f"'{timestamp_column}'가 datetime 타입이 아닙니다. "
                "convert_timestamp()를 먼저 실행하세요."
            ),
        }

    processed_df = (
        processed_df
        .sort_values(
            by=[
                client_column,
                timestamp_column,
            ]
        )
        .reset_index(drop=True)
    )

    return processed_df, {
        "success": True,
        "sort_columns": [
            client_column,
            timestamp_column,
        ],
    }


def create_repeat_count_feature(
    df: pd.DataFrame,
    group_columns: list[str] | None = None,
    feature_name: str = "event_repeat_count"
):
    """
    같은 이벤트가 몇 번 반복되었는지 나타내는 Feature를 생성합니다.

    기본 기준:
    출처별 기본 조합을 사용합니다 (예: H&M t_dat + customer_id + article_id)
    """

    if df is None:
        return None, {
            "success": False,
            "error": "DataFrame이 None입니다.",
        }

    processed_df = df.copy()

    if group_columns is None:
        group_columns = default_repeat_group_columns(processed_df)

    missing_columns = [
        column
        for column in group_columns
        if column not in processed_df.columns
    ]

    if missing_columns:
        return processed_df, {
            "success": False,
            "error": (
                "반복 횟수 계산에 필요한 컬럼이 없습니다: "
                f"{missing_columns}"
            ),
        }

    if not group_columns:
        return processed_df, {
            "success": False,
            "error": "group_columns가 비어 있습니다.",
        }

    processed_df[feature_name] = (
        processed_df
        .groupby(
            group_columns,
            dropna=False
        )[group_columns[0]]
        .transform("size")
        .astype("int64")
    )

    repeat_rows = int(
        (
            processed_df[feature_name] > 1
        ).sum()
    )

    max_repeat_count = (
        int(processed_df[feature_name].max())
        if len(processed_df) > 0
        else 0
    )

    return processed_df, {
        "success": True,
        "group_columns": group_columns,
        "feature_name": feature_name,
        "repeat_rows": repeat_rows,
        "max_repeat_count": max_repeat_count,
    }


def check_infinite_values(df: pd.DataFrame) -> pd.DataFrame:
    """숫자형 컬럼의 +inf, -inf 개수를 확인합니다."""

    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "column_name",
                "positive_inf_count",
                "negative_inf_count",
                "total_inf_count",
            ]
        )

    numeric_df = df.select_dtypes(
        include=[np.number]
    )

    result = []

    for column in numeric_df.columns:
        positive_inf_count = int(
            np.isposinf(
                numeric_df[column]
            ).sum()
        )

        negative_inf_count = int(
            np.isneginf(
                numeric_df[column]
            ).sum()
        )

        result.append(
            {
                "column_name": column,
                "positive_inf_count": positive_inf_count,
                "negative_inf_count": negative_inf_count,
                "total_inf_count": (
                    positive_inf_count
                    + negative_inf_count
                ),
            }
        )

    return pd.DataFrame(result)


def handle_feature_missing_values(
    df: pd.DataFrame,
    zero_fill_columns: list[str] | None = None,
    median_fill_columns: list[str] | None = None,
    category_fill_columns: list[str] | None = None,
    category_fill_value: str = "Unknown",
):
    """
    Feature Engineering 이후 생긴 결측치를
    컬럼 의미에 따라 선택적으로 처리합니다.
    """

    if df is None:
        return None, {
            "success": False,
            "error": "DataFrame이 None입니다.",
        }

    processed_df = df.copy()

    zero_fill_columns = zero_fill_columns or []
    median_fill_columns = median_fill_columns or []
    category_fill_columns = category_fill_columns or []

    report = {
        "success": True,
        "zero_filled": {},
        "median_filled": {},
        "category_filled": {},
        "skipped_columns": [],
    }

    for column in zero_fill_columns:
        if column not in processed_df.columns:
            report["skipped_columns"].append(column)
            continue

        null_count = int(
            processed_df[column]
            .isna()
            .sum()
        )

        processed_df[column] = (
            processed_df[column]
            .fillna(0)
        )

        report["zero_filled"][column] = null_count

    for column in median_fill_columns:
        if column not in processed_df.columns:
            report["skipped_columns"].append(column)
            continue

        if not pd.api.types.is_numeric_dtype(
            processed_df[column]
        ):
            report["skipped_columns"].append(column)
            continue

        null_count = int(
            processed_df[column]
            .isna()
            .sum()
        )

        median_value = (
            processed_df[column]
            .median()
        )

        processed_df[column] = (
            processed_df[column]
            .fillna(median_value)
        )

        report["median_filled"][column] = {
            "null_count": null_count,
            "median_value": (
                None
                if pd.isna(median_value)
                else float(median_value)
            ),
        }

    for column in category_fill_columns:
        if column not in processed_df.columns:
            report["skipped_columns"].append(column)
            continue

        null_count = int(
            processed_df[column]
            .isna()
            .sum()
        )

        processed_df[column] = (
            processed_df[column]
            .fillna(category_fill_value)
        )

        report["category_filled"][column] = null_count

    return processed_df, report


def replace_infinite_with_nan(
    df: pd.DataFrame
):
    """
    숫자형 컬럼의 +inf, -inf를 NaN으로 변환합니다.
    이후 컬럼 의미에 맞는 결측치 처리 함수를 적용합니다.
    """

    if df is None:
        return None, {
            "success": False,
            "error": "DataFrame이 None입니다.",
        }

    processed_df = df.copy()

    numeric_columns = (
        processed_df
        .select_dtypes(
            include=[np.number]
        )
        .columns
        .tolist()
    )

    replaced_counts = {}

    for column in numeric_columns:
        inf_mask = np.isinf(
            processed_df[column]
        )

        inf_count = int(
            inf_mask.sum()
        )

        if inf_count > 0:
            processed_df.loc[
                inf_mask,
                column
            ] = np.nan

        replaced_counts[column] = inf_count

    return processed_df, {
        "success": True,
        "replaced_counts": replaced_counts,
    }
