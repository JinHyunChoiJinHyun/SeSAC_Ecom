"""
CART SIGNAL Model Profile Registry

현재 24/48/72시간 구매예측 모델은 modeling.config에서 자동 등록하고,
향후 새 예측 모델은 modeling/model_profiles.json에 추가합니다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from modeling.config import (
    FEATURE_COLUMNS,
    PREDICTION_HOURS,
    PREDICTION_MODEL_NAME,
    PURCHASE_DEADLINE_DAYS,
    get_model_name,
)

EXTRA_PROFILE_PATH = Path(__file__).resolve().with_name("model_profiles.json")


class ModelProfileError(ValueError):
    """Model Profile 설정/선택 오류."""


@dataclass(frozen=True)
class ModelProfile:
    key: str
    task: str
    target: str
    event: str
    runner: str
    model_path: str
    feature_source: str
    feature_columns: tuple[str, ...]
    prediction_horizon: str
    observation_window_hours: int | None = None
    enabled: bool = True
    description: str = ""
    model_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["feature_columns"] = list(self.feature_columns)
        return result


def _relative_model_path(model_name: str) -> str:
    return str(Path("modeling") / "models" / f"{model_name}.joblib")


def _build_builtin_profiles() -> dict[str, ModelProfile]:
    """현재 24/48/72시간 구매예측 모델을 자동 등록합니다."""

    profiles: dict[str, ModelProfile] = {}

    for hour in PREDICTION_HOURS:
        hour = int(hour)
        saved_model_name = get_model_name(
            PREDICTION_MODEL_NAME,
            hour,
        )

        key = f"cart_purchase_{hour}h"

        profiles[key] = ModelProfile(
            key=key,
            task="purchase_prediction",
            target="same_product_purchase",
            event="add_to_cart",
            runner="purchase_prediction",
            model_path=_relative_model_path(saved_model_name),
            feature_source="modeling.features",
            feature_columns=tuple(FEATURE_COLUMNS),
            prediction_horizon=f"{PURCHASE_DEADLINE_DAYS}d",
            observation_window_hours=hour,
            enabled=True,
            description=(
                f"장바구니 추가 후 {hour}시간까지의 행동으로 "
                f"{PURCHASE_DEADLINE_DAYS}일 이내 동일 상품 구매 가능성을 예측"
            ),
            model_version=(
                f"{PREDICTION_MODEL_NAME}-{hour}h-"
                f"deadline-{PURCHASE_DEADLINE_DAYS}d"
            ),
        )

    return profiles


def _profile_from_dict(raw: dict[str, Any]) -> ModelProfile:
    required = {
        "key",
        "task",
        "target",
        "event",
        "runner",
        "model_path",
        "feature_source",
        "prediction_horizon",
    }

    missing = required - set(raw)

    if missing:
        raise ModelProfileError(
            f"Model Profile 필수 항목이 없습니다: {sorted(missing)}"
        )

    observation_window = raw.get("observation_window_hours")

    if observation_window is not None:
        observation_window = int(observation_window)

    return ModelProfile(
        key=str(raw["key"]),
        task=str(raw["task"]),
        target=str(raw["target"]),
        event=str(raw["event"]),
        runner=str(raw["runner"]),
        model_path=str(raw["model_path"]),
        feature_source=str(raw["feature_source"]),
        feature_columns=tuple(
            str(column)
            for column in (raw.get("feature_columns") or [])
        ),
        prediction_horizon=str(raw["prediction_horizon"]),
        observation_window_hours=observation_window,
        enabled=bool(raw.get("enabled", True)),
        description=str(raw.get("description", "")),
        model_version=str(raw.get("model_version", "")),
    )


def _load_extra_profiles() -> dict[str, ModelProfile]:
    """JSON에 추가한 Profile을 읽습니다."""

    if not EXTRA_PROFILE_PATH.is_file():
        return {}

    payload = json.loads(
        EXTRA_PROFILE_PATH.read_text(encoding="utf-8")
    )

    profiles: dict[str, ModelProfile] = {}

    for raw in payload.get("profiles", []) or []:
        profile = _profile_from_dict(raw)
        profiles[profile.key] = profile

    return profiles


def load_model_profiles(
    include_disabled: bool = False,
) -> dict[str, ModelProfile]:
    """기본 Profile과 JSON 추가 Profile을 합칩니다."""

    profiles = _build_builtin_profiles()
    profiles.update(_load_extra_profiles())

    if include_disabled:
        return profiles

    return {
        key: profile
        for key, profile in profiles.items()
        if profile.enabled
    }


def get_model_profile(
    model_key: str,
    include_disabled: bool = False,
) -> ModelProfile:
    profiles = load_model_profiles(
        include_disabled=include_disabled
    )

    if model_key not in profiles:
        raise ModelProfileError(
            f"등록된 Model Profile이 없습니다: {model_key}"
        )

    return profiles[model_key]


def resolve_model_profile(
    *,
    task: str,
    prediction_hour: int | None = None,
    target: str | None = None,
) -> ModelProfile:
    """
    task / target / prediction_hour로 Profile을 결정합니다.
    후보가 여러 개면 임의 선택하지 않습니다.
    """

    candidates = [
        profile
        for profile in load_model_profiles().values()
        if profile.task == task
    ]

    if target is not None:
        candidates = [
            profile
            for profile in candidates
            if profile.target == target
        ]

    if prediction_hour is not None:
        candidates = [
            profile
            for profile in candidates
            if profile.observation_window_hours == int(prediction_hour)
        ]

    if not candidates:
        raise ModelProfileError(
            "조건에 맞는 Model Profile이 없습니다. "
            f"task={task}, target={target}, "
            f"prediction_hour={prediction_hour}"
        )

    if len(candidates) > 1:
        keys = [profile.key for profile in candidates]
        raise ModelProfileError(
            "Model Profile 후보가 여러 개입니다. "
            f"model_key 또는 추가 조건이 필요합니다: {keys}"
        )

    return candidates[0]


def list_model_profiles(
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    return [
        profile.to_dict()
        for profile in load_model_profiles(
            include_disabled=include_disabled
        ).values()
    ]
