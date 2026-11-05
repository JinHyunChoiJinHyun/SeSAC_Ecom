"""
Model Agent 실제 실행 Tool

CART SIGNAL 기존 모델링 구조에 맞춘 버전입니다.

기존 프로젝트 구조
-----------------
- 모델 파일:
    modeling/models/*.joblib

- 모델 성능 리포트:
    modeling/reports/model_comparison_deadline_7d.csv

- Feature / 모델 경로 / 모델명 규칙:
    config.py

별도의 models/model_registry.json 파일은 사용하지 않습니다.
기존 코드와의 호환을 위해 load_model_registry(), get_model_spec()
함수 이름은 유지하지만, 실제 설정은 config.py와 성능 리포트에서 동적으로 구성합니다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from modeling.config import (
    PROJECT_ROOT,
    MODEL_DIR,
    REPORT_DIR,
    FEATURE_COLUMNS,
    ID_COLUMN,
    PREDICTION_HOUR_COLUMN,
    PREDICTION_HOURS,
    PURCHASE_DEADLINE_DAYS,
    PREDICTION_MODEL_NAME,
    get_model_name,
)


# =========================================================
# 기본 설정
# =========================================================

MODEL_REPORT_PATH = (
    REPORT_DIR
    / f"model_comparison_deadline_{PURCHASE_DEADLINE_DAYS}d.csv"
)

# ModelPlan에 prediction_hour 필드가 아직 없을 때 사용할 기본값입니다.
# 실제 DataFrame에 hours_after_cart가 있으면 그 값을 우선 사용합니다.
DEFAULT_PREDICTION_HOUR = min(PREDICTION_HOURS)

# LightGBM 학습 시 범주형으로 사용한 Feature입니다.
# product_category는 실제 상품 카테고리 ID이므로 연속형 수치가 아니라
# 범주형 Feature로 취급합니다.
CATEGORICAL_FEATURE_COLUMNS = [
    "product_category",
]


# =========================================================
# 공통 Helper
# =========================================================

def _validate_prediction_hour(
    prediction_hour: int,
) -> int:
    """지원하는 예측 시점인지 확인합니다."""

    hour = int(
        prediction_hour
    )

    if hour not in PREDICTION_HOURS:

        raise ValueError(
            f"지원하지 않는 예측 시점입니다: {hour}. "
            f"지원 값: {PREDICTION_HOURS}"
        )

    return hour


def _parse_prediction_hour_from_model_name(
    model_name: str | None,
) -> int | None:
    """
    저장 모델 이름에서 예측 시점을 읽습니다.

    예:
    lightgbm_24h_deadline_7d
    → 24
    """

    if not model_name:

        return None

    match = re.search(
        r"_(\d+)h(?:_|$)",
        str(
            model_name
        )
    )

    if not match:

        return None

    return int(
        match.group(
            1
        )
    )


def _base_model_name(
    model_name: str | None,
) -> str:
    """
    Agent가 전달한 모델명을
    기존 모델링 코드의 base model 이름으로 변환합니다.

    champion / None
    → config.PREDICTION_MODEL_NAME
    """

    if (
        model_name is None
        or model_name == ""
        or str(model_name).lower() == "champion"
    ):

        return PREDICTION_MODEL_NAME


    name = str(
        model_name
    )


    # lightgbm_24h_deadline_7d 같은 실제 파일명을 받았다면
    # 앞의 base model 이름만 추출합니다.
    match = re.match(
        r"(.+?)_\d+h_deadline_\d+d$",
        name,
    )


    if match:

        return match.group(
            1
        )


    return name


def _model_path(
    base_model_name: str,
    prediction_hour: int,
) -> Path:
    """기존 config.py 파일명 규칙으로 모델 경로를 반환합니다."""

    hour = _validate_prediction_hour(
        prediction_hour
    )


    saved_model_name = (
        get_model_name(
            base_model_name,
            hour,
        )
    )


    path = (
        MODEL_DIR
        / f"{saved_model_name}.joblib"
    )


    if not path.is_file():

        raise FileNotFoundError(
            "모델 파일을 찾지 못했습니다: "
            f"{path}"
        )


    return path


def _load_model_report() -> pd.DataFrame:
    """기존 모델 성능 비교 CSV를 읽습니다."""

    if not MODEL_REPORT_PATH.is_file():

        raise FileNotFoundError(
            "모델 성능 리포트를 찾지 못했습니다: "
            f"{MODEL_REPORT_PATH}"
        )


    report = pd.read_csv(
        MODEL_REPORT_PATH
    )


    required_columns = {
        "prediction_hour",
        "model",
        "pr_auc",
        "roc_auc",
        "brier_score",
    }


    missing = (
        required_columns
        - set(
            report.columns
        )
    )


    if missing:

        raise ValueError(
            "모델 성능 리포트에 필요한 컬럼이 없습니다: "
            f"{sorted(missing)}"
        )


    return report


def _metrics_for_model(
    report: pd.DataFrame,
    base_model_name: str,
    prediction_hour: int,
) -> dict:
    """특정 모델/예측 시점의 저장된 검증 성능을 반환합니다."""

    matched = report[
        (
            report[
                "prediction_hour"
            ]
            == int(
                prediction_hour
            )
        )
        &
        (
            report[
                "model"
            ]
            == base_model_name
        )
    ]


    if matched.empty:

        return {}


    row = (
        matched
        .iloc[0]
    )


    metrics = {}


    for column in [
        "positive_rate",
        "pr_auc",
        "roc_auc",
        "brier_score",
    ]:

        if (
            column
            in matched.columns
            and pd.notna(
                row[
                    column
                ]
            )
        ):

            metrics[
                column
            ] = float(
                row[
                    column
                ]
            )


    return metrics


# =========================================================
# Registry 호환 API
# =========================================================

def load_model_registry(
    registry_path: str | Path | None = None,
) -> dict:
    """
    기존 model_registry.json 대신
    config.py + model comparison report를 사용하여
    동적으로 Registry 형태의 dict를 만듭니다.

    registry_path는 기존 코드 호환을 위해 남겨둔 인자이며
    현재 버전에서는 사용하지 않습니다.
    """

    report = (
        _load_model_report()
    )


    models: dict[str, dict] = {}


    for hour in PREDICTION_HOURS:

        saved_model_name = (
            get_model_name(
                PREDICTION_MODEL_NAME,
                hour,
            )
        )


        models[
            saved_model_name
        ] = {

            "path":
                str(
                    MODEL_DIR
                    / f"{saved_model_name}.joblib"
                ),

            "version":
                (
                    f"{PREDICTION_MODEL_NAME}-"
                    f"{hour}h-"
                    f"deadline-{PURCHASE_DEADLINE_DAYS}d"
                ),

            "base_model_name":
                PREDICTION_MODEL_NAME,

            "prediction_hour":
                int(
                    hour
                ),

            "feature_columns":
                list(
                    FEATURE_COLUMNS
                ),

            "id_columns": [
                ID_COLUMN,
                "client_id",
                "sku",
                PREDICTION_HOUR_COLUMN,
            ],

            "positive_label":
                1,

            "threshold":
                None,

            "prediction_column":
                "purchase_probability",

            "class_column":
                "predicted_purchase",

            "observation_window":
                f"{hour}h",

            "prediction_horizon":
                (
                    f"장바구니 시점부터 "
                    f"{PURCHASE_DEADLINE_DAYS}일 이내 구매"
                ),

            "metrics":
                _metrics_for_model(
                    report=
                        report,

                    base_model_name=
                        PREDICTION_MODEL_NAME,

                    prediction_hour=
                        hour,
                ),
        }


    # 기존 get_model_spec("champion") 호환을 위해
    # 기본 Champion은 가장 이른 예측 시점의 설정으로 둡니다.
    #
    # 실제 구매 예측에서는 DataFrame의 hours_after_cart가 있으면
    # 이 기본값보다 DataFrame 값을 우선합니다.
    default_champion = (
        get_model_name(
            PREDICTION_MODEL_NAME,
            DEFAULT_PREDICTION_HOUR,
        )
    )


    return {

        "champion":
            default_champion,

        "configured_model":
            PREDICTION_MODEL_NAME,

        "default_prediction_hour":
            DEFAULT_PREDICTION_HOUR,

        "models":
            models,
    }


def get_model_spec(
    model_name: str | None = "champion",
    registry: dict | None = None,
) -> tuple[str, dict]:
    """
    요청 모델 설정을 반환합니다.

    champion
    → 기본 24h LightGBM 설정

    lightgbm_48h_deadline_7d
    → 해당 저장 모델 설정
    """

    registry = (
        registry
        or load_model_registry()
    )


    models = (
        registry.get(
            "models",
            {}
        )
    )


    if (
        not model_name
        or model_name == "champion"
    ):

        selected_name = (
            registry.get(
                "champion"
            )
        )

    else:

        selected_name = str(
            model_name
        )


        # base model 이름만 받은 경우
        # 기본 prediction hour 모델로 연결
        if selected_name not in models:

            possible_name = (
                get_model_name(
                    _base_model_name(
                        selected_name
                    ),
                    DEFAULT_PREDICTION_HOUR,
                )
            )


            if possible_name in models:

                selected_name = (
                    possible_name
                )


    if not selected_name:

        raise ValueError(
            "Champion 모델이 지정되지 않았습니다."
        )


    if selected_name not in models:

        raise KeyError(
            "현재 Model Agent Registry에 모델이 없습니다: "
            f"{selected_name}"
        )


    return (
        selected_name,
        models[
            selected_name
        ],
    )


# =========================================================
# 모델 로딩 / Feature
# =========================================================

def load_serialized_model(
    spec: dict,
):
    """Registry 호환 spec에서 실제 joblib 모델을 불러옵니다."""

    raw_path = (
        spec.get(
            "path"
        )
    )


    if not raw_path:

        raise ValueError(
            "모델 path가 설정에 없습니다."
        )


    path = Path(
        raw_path
    )


    if not path.is_absolute():

        path = (
            PROJECT_ROOT
            / path
        )


    if not path.is_file():

        raise FileNotFoundError(
            "모델 파일을 찾지 못했습니다: "
            f"{path}"
        )


    return joblib.load(
        path
    )


def resolve_feature_columns(
    model,
    spec: dict,
) -> list[str]:
    """
    현재 프로젝트에서는 config.FEATURE_COLUMNS를 기준으로 사용합니다.

    Registry 호환 spec에 Feature가 있으면
    그 값을 우선 사용합니다.
    """

    feature_columns = (
        spec.get(
            "feature_columns"
        )
        or []
    )


    if feature_columns:

        return [
            str(
                column
            )
            for column
            in feature_columns
        ]


    if hasattr(
        model,
        "feature_names_in_"
    ):

        return [
            str(
                column
            )
            for column
            in model.feature_names_in_
        ]


    return list(
        FEATURE_COLUMNS
    )


def validate_feature_columns(
    df: pd.DataFrame,
    feature_columns: list[str],
) -> list[str]:
    """입력 DataFrame에서 누락된 모델 Feature를 반환합니다."""

    return [
        column

        for column
        in feature_columns

        if column
        not in df.columns
    ]


def _prepare_prediction_features(
    model,
    x: pd.DataFrame,
) -> pd.DataFrame:
    """
    저장된 LightGBM 모델의 학습 당시 범주형 정보를 기준으로
    예측 입력 DataFrame의 categorical dtype을 맞춥니다.

    LightGBM은 pandas category Feature로 학습한 경우
    예측 시에도 동일한 categorical Feature 구조를 기대합니다.

    현재 CART SIGNAL에서는 product_category를 범주형으로 사용합니다.
    """

    prepared = x.copy()

    # LightGBM sklearn 모델이 아니거나,
    # 저장된 범주형 메타데이터가 없다면 그대로 반환합니다.
    booster = getattr(
        model,
        "booster_",
        None,
    )

    if booster is None:
        return prepared

    pandas_categorical = getattr(
        booster,
        "pandas_categorical",
        None,
    )

    if not pandas_categorical:
        return prepared

    categorical_columns = [
        column
        for column in CATEGORICAL_FEATURE_COLUMNS
        if column in prepared.columns
    ]

    # 학습 당시 범주형 Feature 개수와 현재 설정이 다르면
    # 잘못된 예측을 진행하지 않고 원인을 명확히 알립니다.
    if len(categorical_columns) != len(pandas_categorical):
        raise ValueError(
            "LightGBM 학습 당시 범주형 Feature 수와 "
            "현재 예측 설정이 일치하지 않습니다. "
            f"설정 컬럼={categorical_columns}, "
            f"학습 범주형 개수={len(pandas_categorical)}"
        )

    # 학습 당시 category 목록까지 동일하게 복원합니다.
    # 새로운 카테고리가 들어오면 pandas에서 NaN category로 처리되며,
    # LightGBM은 이를 missing 값으로 처리합니다.
    for column, categories in zip(
        categorical_columns,
        pandas_categorical,
    ):
        prepared[column] = pd.Categorical(
            prepared[column],
            categories=categories,
        )

    return prepared


def _get_positive_probability(
    model,
    x: pd.DataFrame,
    positive_label: Any = 1,
) -> np.ndarray:
    """sklearn / LightGBM 모델의 positive 확률을 반환합니다."""

    if not hasattr(
        model,
        "predict_proba"
    ):

        raise ValueError(
            "현재 저장 모델은 predict_proba()를 지원하지 않습니다."
        )


    probabilities = np.asarray(
        model.predict_proba(
            x
        )
    )


    if (
        probabilities.ndim != 2
        or probabilities.shape[1] < 1
    ):

        raise ValueError(
            "predict_proba() 결과 형식이 예상과 다릅니다."
        )


    if probabilities.shape[1] == 1:

        return probabilities[
            :,
            0
        ]


    positive_index = 1


    classes = getattr(
        model,
        "classes_",
        None,
    )


    if (
        classes is not None
        and positive_label
        in list(
            classes
        )
    ):

        positive_index = (
            list(
                classes
            )
            .index(
                positive_label
            )
        )


    return probabilities[
        :,
        positive_index
    ]


# =========================================================
# 예측 시점 결정
# =========================================================

def _resolve_prediction_hours(
    df: pd.DataFrame,
    model_name: str | None,
) -> list[int]:
    """
    예측 시점을 결정합니다.

    우선순위:
    1. DataFrame의 hours_after_cart
    2. model_name에 포함된 24h/48h/72h
    3. 기본 24h
    """

    if (
        PREDICTION_HOUR_COLUMN
        in df.columns
    ):

        values = (
            pd.to_numeric(
                df[
                    PREDICTION_HOUR_COLUMN
                ],
                errors="coerce",
            )
            .dropna()
            .astype(
                int
            )
            .unique()
            .tolist()
        )


        if values:

            invalid = [
                hour

                for hour
                in values

                if hour
                not in PREDICTION_HOURS
            ]


            if invalid:

                raise ValueError(
                    "DataFrame에 지원하지 않는 hours_after_cart 값이 있습니다: "
                    f"{invalid}"
                )


            return sorted(
                values
            )


    parsed_hour = (
        _parse_prediction_hour_from_model_name(
            model_name
        )
    )


    if parsed_hour is not None:

        return [
            _validate_prediction_hour(
                parsed_hour
            )
        ]


    return [
        DEFAULT_PREDICTION_HOUR
    ]


# =========================================================
# 구매확률 예측
# =========================================================

def predict_purchase(
    df: pd.DataFrame,
    model_name: str | None = "champion",
    threshold: float | None = None,
    prediction_hour: int | None = None,
) -> dict:
    """
    기존 CART SIGNAL 저장 모델을 사용해 구매확률을 예측합니다.

    DataFrame에 hours_after_cart가 있으면
    24h / 48h / 72h 모델을 자동으로 나누어 적용합니다.

    threshold는 선택값입니다.
    현재 프로젝트의 기본 운영은 확률 순위 기반 segment이므로,
    threshold가 전달되지 않으면 강제로 0.5 분류를 만들지 않습니다.
    """

    if not isinstance(
        df,
        pd.DataFrame
    ):

        return {
            "success":
                False,

            "error":
                "입력 데이터가 pandas DataFrame이 아닙니다.",
        }


    if df.empty:

        return {
            "success":
                False,

            "error":
                "예측할 DataFrame이 비어 있습니다.",
        }


    try:

        feature_columns = list(
            FEATURE_COLUMNS
        )


        missing_columns = (
            validate_feature_columns(
                df=
                    df,

                feature_columns=
                    feature_columns,
            )
        )


        if missing_columns:

            return {

                "success":
                    False,

                "missing_features":
                    missing_columns,

                "required_features":
                    feature_columns,

                "error":
                    (
                        "모델 예측에 필요한 Feature가 부족합니다: "
                        f"{missing_columns}"
                    ),
            }


        base_model_name = (
            _base_model_name(
                model_name
            )
        )


        if prediction_hour is not None:
            prediction_hours = [
                _validate_prediction_hour(
                    prediction_hour
                )
            ]
        else:
            prediction_hours = (
                _resolve_prediction_hours(
                    df=
                        df,

                    model_name=
                        model_name,
                )
            )


        result_parts: list[
            pd.DataFrame
        ] = []


        model_names_used: list[
            str
        ] = []


        metrics_by_hour: dict[
            int,
            dict
        ] = {}


        try:
            report = (
                _load_model_report()
            )
        except (
            FileNotFoundError,
            ValueError,
            pd.errors.EmptyDataError,
        ):
            report = None


        for hour in prediction_hours:

            # DataFrame에 hours_after_cart가 있으면
            # 현재 hour 행만 예측합니다.
            if (
                PREDICTION_HOUR_COLUMN
                in df.columns
            ):

                hour_mask = (
                    pd.to_numeric(
                        df[
                            PREDICTION_HOUR_COLUMN
                        ],
                        errors="coerce",
                    )
                    == hour
                )


                hour_df = (
                    df.loc[
                        hour_mask
                    ]
                    .copy()
                )

            else:

                hour_df = (
                    df.copy()
                )


            if hour_df.empty:

                continue


            saved_model_name = (
                get_model_name(
                    base_model_name,
                    hour,
                )
            )


            model_path = (
                _model_path(
                    base_model_name=
                        base_model_name,

                    prediction_hour=
                        hour,
                )
            )


            model = (
                joblib.load(
                    model_path
                )
            )


            x = (
                hour_df[
                    feature_columns
                ]
                .copy()
            )

            # LightGBM 학습 당시 범주형 Feature 구조와
            # 현재 예측 입력의 dtype/category 정보를 맞춥니다.
            x = _prepare_prediction_features(
                model=model,
                x=x,
            )


            probabilities = (
                _get_positive_probability(
                    model=
                        model,

                    x=
                        x,

                    positive_label=
                        1,
                )
            )


            keep_columns = [
                column

                for column
                in [
                    ID_COLUMN,
                    "client_id",
                    "sku",
                    PREDICTION_HOUR_COLUMN,
                ]

                if column
                in hour_df.columns
            ]


            result_df = (
                hour_df[
                    keep_columns
                ]
                .copy()
            )


            result_df[
                "purchase_probability"
            ] = probabilities


            result_df[
                "model_name"
            ] = (
                saved_model_name
            )


            if threshold is not None:

                selected_threshold = float(
                    threshold
                )


                result_df[
                    "predicted_purchase"
                ] = (
                    probabilities
                    >= selected_threshold
                ).astype(
                    int
                )


            result_parts.append(
                result_df
            )


            model_names_used.append(
                saved_model_name
            )


            metrics_by_hour[
                int(
                    hour
                )
            ] = (
                _metrics_for_model(
                    report=
                        report,

                    base_model_name=
                        base_model_name,

                    prediction_hour=
                        hour,
                )
                if report is not None
                else {}
            )


        if not result_parts:

            return {

                "success":
                    False,

                "error":
                    "예측 가능한 행이 없습니다.",
            }


        result = (
            pd.concat(
                result_parts,
                ignore_index=True,
            )
            .sort_values(
                "purchase_probability",
                ascending=False,
            )
            .reset_index(
                drop=True
            )
        )


        model_label = (
            model_names_used[
                0
            ]

            if len(
                model_names_used
            )
            == 1

            else ", ".join(
                model_names_used
            )
        )


        return {

            "success":
                True,

            "model_name":
                model_label,

            "model_version":
                (
                    f"{base_model_name}-"
                    f"deadline-{PURCHASE_DEADLINE_DAYS}d"
                ),

            "prediction_dataframe":
                result,

            "feature_columns":
                feature_columns,

            "prediction_hours":
                prediction_hours,

            "threshold":
                (
                    float(
                        threshold
                    )
                    if threshold is not None
                    else None
                ),

            "positive_label":
                1,

            "metrics":
                metrics_by_hour,

            "observation_window":
                [
                    f"{hour}h"
                    for hour
                    in prediction_hours
                ],

            "prediction_horizon":
                (
                    f"장바구니 시점부터 "
                    f"{PURCHASE_DEADLINE_DAYS}일 이내 구매"
                ),

            "error":
                "",
        }


    except Exception as exc:

        return {

            "success":
                False,

            "error":
                repr(
                    exc
                ),
        }


# =========================================================
# 모델 성능 비교
# =========================================================

def get_model_comparison(
    registry: dict | None = None,
) -> pd.DataFrame:
    """
    기존 학습 리포트를 그대로 읽어 모델 성능을 비교합니다.

    중요한 점:
    24h / 48h / 72h는 positive rate 자체가 다르므로
    서로 다른 예측 시점의 PR-AUC를 단순 비교해
    '24h가 무조건 더 좋은 모델'처럼 해석하면 안 됩니다.

    동일 prediction_hour 안에서 모델을 비교하는 것이 기본입니다.
    """

    report = (
        _load_model_report()
        .copy()
    )


    # 동일 예측 시점 안에서 PR-AUC가 가장 높은 모델 표시
    report[
        "is_best_pr_auc"
    ] = False


    for hour in sorted(
        report[
            "prediction_hour"
        ]
        .dropna()
        .unique()
    ):

        hour_mask = (
            report[
                "prediction_hour"
            ]
            == hour
        )


        hour_rows = (
            report.loc[
                hour_mask
            ]
        )


        if hour_rows.empty:

            continue


        best_index = (
            hour_rows[
                "pr_auc"
            ]
            .astype(
                float
            )
            .idxmax()
        )


        report.loc[
            best_index,
            "is_best_pr_auc",
        ] = True


    report[
        "is_configured_prediction_model"
    ] = (
        report[
            "model"
        ]
        == PREDICTION_MODEL_NAME
    )


    preferred_columns = [
        "prediction_hour",
        "model",
        "is_best_pr_auc",
        "is_configured_prediction_model",
        "train_rows",
        "test_rows",
        "positive_rate",
        "pr_auc",
        "roc_auc",
        "brier_score",
    ]


    return report[
        [
            column

            for column
            in preferred_columns

            if column
            in report.columns
        ]
    ]


# =========================================================
# 기존 뼈대 함수명 호환
# =========================================================

def compare_models(
    champion_model=None,
    candidate_model=None,
    df=None,
):
    """기존 함수명 호환."""

    return get_model_comparison()


def predict_churn(
    df: pd.DataFrame,
):
    """현재 프로젝트 범위에서는 이탈 예측 미지원."""

    raise NotImplementedError(
        "현재 Model Agent 기본 구현은 구매확률 예측만 지원합니다."
    )


def evaluate_model(
    model,
    df,
):
    """
    모델 평가는 기존 train.py 학습/검증 파이프라인에서 수행합니다.
    """

    raise NotImplementedError(
        "모델 평가는 modeling/train.py의 검증 결과를 사용합니다."
    )


def train_candidate_model(
    df,
):
    """
    Agent가 임의로 재학습하지 않도록 제한합니다.
    """

    raise NotImplementedError(
        "모델 학습/재학습은 현재 Model Agent 범위에 포함하지 않습니다."
    )
