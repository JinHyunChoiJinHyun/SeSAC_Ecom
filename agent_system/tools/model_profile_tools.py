"""
Model Profile 기반 범용 예측 실행 Tool

기존 model_tools.predict_purchase()는 유지하고,
새로운 model_key 기반 진입점을 제공합니다.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pandas as pd

from agent_system.tools.model_tools import predict_purchase
from modeling.model_registry import (
    ModelProfile,
    get_model_profile,
    resolve_model_profile,
)


def _resolve_hour_from_dataframe(
    df: pd.DataFrame,
) -> int | None:
    column = "hours_after_cart"

    if column not in df.columns:
        return None

    values = (
        pd.to_numeric(df[column], errors="coerce")
        .dropna()
        .astype(int)
        .unique()
        .tolist()
    )

    if len(values) == 1:
        return int(values[0])

    return None


def _load_python_runner(runner: str):
    """python:module.path:function_name 형식의 runner를 불러옵니다."""

    prefix = "python:"

    if not runner.startswith(prefix):
        raise ValueError(
            f"Python runner 형식이 아닙니다: {runner}"
        )

    target = runner[len(prefix):]

    try:
        module_name, function_name = target.rsplit(":", 1)
    except ValueError as exc:
        raise ValueError(
            "runner는 python:module.path:function_name 형식이어야 합니다."
        ) from exc

    module = import_module(module_name)
    function = getattr(module, function_name, None)

    if function is None:
        raise AttributeError(
            f"등록된 runner 함수를 찾을 수 없습니다: {target}"
        )

    return function


def _resolve_profile(
    *,
    df: pd.DataFrame,
    model_key: str | None,
    task: str,
    target: str | None,
    prediction_hour: int | None,
) -> ModelProfile | None:

    if model_key:
        return get_model_profile(model_key)

    selected_hour = prediction_hour

    if selected_hour is None:
        selected_hour = _resolve_hour_from_dataframe(df)

    # 여러 hour가 섞여 있으면 기존 predict_purchase의
    # multi-hour 처리를 그대로 사용합니다.
    if selected_hour is None:
        return None

    return resolve_model_profile(
        task=task,
        prediction_hour=int(selected_hour),
        target=target,
    )


def run_prediction_profile(
    df: pd.DataFrame,
    *,
    model_key: str | None = None,
    task: str = "purchase_prediction",
    target: str | None = "same_product_purchase",
    prediction_hour: int | None = None,
    model_name: str | None = "champion",
    threshold: float | None = None,
    **kwargs: Any,
) -> dict:
    """
    범용 Model Profile 실행 진입점.

    현재 구매예측은 기존 predict_purchase()를 재사용합니다.
    향후 Profile은 model_profiles.json의 runner만 등록하면 됩니다.
    """

    if not isinstance(df, pd.DataFrame):
        return {
            "success": False,
            "error": "입력 데이터가 pandas DataFrame이 아닙니다.",
        }

    if df.empty:
        return {
            "success": False,
            "error": "예측할 DataFrame이 비어 있습니다.",
        }

    try:
        profile = _resolve_profile(
            df=df,
            model_key=model_key,
            task=task,
            target=target,
            prediction_hour=prediction_hour,
        )

        # 기존 multi-hour 동작 호환
        if profile is None:
            result = predict_purchase(
                df=df,
                model_name=model_name,
                threshold=threshold,
                prediction_hour=prediction_hour,
            )

            if result.get("success"):
                hours = result.get("prediction_hours", []) or []
                result["model_keys"] = [
                    f"cart_purchase_{int(hour)}h"
                    for hour in hours
                ]
                result["model_profile_mode"] = "legacy_multi_hour"

            return result

        if profile.runner == "purchase_prediction":
            result = predict_purchase(
                df=df,
                model_name=model_name,
                threshold=threshold,
                prediction_hour=profile.observation_window_hours,
            )

        elif profile.runner.startswith("python:"):
            runner = _load_python_runner(profile.runner)

            result = runner(
                df=df,
                profile=profile,
                threshold=threshold,
                **kwargs,
            )

        else:
            raise ValueError(
                f"지원하지 않는 Model Profile runner입니다: {profile.runner}"
            )

        if not isinstance(result, dict):
            raise TypeError(
                "Model runner는 dict 결과를 반환해야 합니다."
            )

        result["model_key"] = profile.key
        result["model_profile"] = profile.to_dict()

        return result

    except Exception as exc:
        return {
            "success": False,
            "error": repr(exc),
            "model_key": model_key,
        }
