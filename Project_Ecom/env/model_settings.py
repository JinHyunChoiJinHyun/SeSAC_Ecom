"""대시보드와 봇이 공유하는 채팅 모델 설정 및 요청별 고정 설정입니다."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
from threading import RLock

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("agent_system")
MODEL_OPTIONS = {
    "openai": {"model": "gpt-4o-mini", "label": "GPT", "key_env": "OPENAI_API_KEY"},
    "nvidia": {
        "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "label": "NVIDIA Nemotron", "key_env": "NVIDIA_API_KEY",
    },
    "gemini": {
        "model": "gemini-3.1-flash-lite",
        "label": "Gemini", "key_env": "GEMINI_API_KEY",
    },
}


class ModelSettingsError(ValueError):
    """인증정보를 포함하지 않는 모델 설정 오류입니다."""


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"

    def public(self):
        return {"provider": self.provider, "model": self.model}


def validate_selection(value):
    """파일·API·State에 들어온 제공 업체와 모델 조합을 검증합니다."""
    if isinstance(value, ModelConfig):
        value = value.public()
    if not isinstance(value, dict) or set(value) != {"provider", "model"}:
        raise ModelSettingsError("제공 업체와 모델을 선택해 주세요.")
    provider, model = value["provider"], value["model"]
    if not isinstance(provider, str) or not isinstance(model, str):
        raise ModelSettingsError("올바른 모델 설정이 아닙니다.")
    if provider not in MODEL_OPTIONS or model != MODEL_OPTIONS[provider]["model"]:
        raise ModelSettingsError("허용되지 않은 제공 업체 또는 모델입니다.")
    return ModelConfig(provider, model)


def environment_value(name, project=PROJECT_ROOT):
    """기존 환경변수를 우선하고 지정 프로젝트의 .env를 읽습니다. 값은 공개하지 않습니다."""
    if name in os.environ:
        return os.environ[name]
    return dotenv_values(Path(project) / "env" / ".env").get(name) or ""


def require_api_key(config, project=PROJECT_ROOT):
    config = validate_selection(config)
    key_name = MODEL_OPTIONS[config.provider]["key_env"]
    key = environment_value(key_name, project).strip()
    if not key:
        raise ModelSettingsError(f"{key_name}가 설정되지 않아 모델을 사용할 수 없습니다.")
    return key


class ModelSettingsStore:
    """원자적 JSON 저장과 프로세스별 마지막 정상 설정을 관리합니다."""

    def __init__(self, project=PROJECT_ROOT):
        self.project = Path(project).resolve()
        self.path = self.project / "runtime" / "llm_settings.json"
        self.lock = RLock()
        self._last_good = None
        self._seen_file = False
        self.warning = None

    def current(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                config = validate_selection(value)
                self._seen_file = True
            except FileNotFoundError:
                if not self._seen_file:
                    config = ModelConfig()
                else:
                    return self._invalid("모델 설정 파일이 사라졌습니다.")
            except (OSError, UnicodeError, ValueError, TypeError):
                return self._invalid("모델 설정 파일을 읽지 못했습니다.")
            self._last_good = config
            self.warning = None
            return config

    def _invalid(self, message):
        self.warning = message + " 마지막 정상 설정을 유지합니다." if self._last_good else message + " 요청 처리를 중단합니다."
        LOGGER.error("LLM_SETTINGS_ERROR | %s", self.warning)
        if self._last_good is None:
            # 현재 저장소 인스턴스에 마지막 정상 설정이 없으므로 GPT로 조용히 대체하지 않습니다.
            raise ModelSettingsError(self.warning)
        return self._last_good

    def apply(self, value):
        config = validate_selection(value)
        require_api_key(config, self.project)
        with self.lock:
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix="llm_settings.", suffix=".tmp", delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump(config.public(), handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except OSError:
                LOGGER.error("LLM_SETTINGS_SAVE_FAILED")
                raise ModelSettingsError("모델 설정을 저장하지 못했습니다. 기존 설정을 유지합니다.") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self._last_good = config
            self._seen_file = True
            self.warning = None
            return config

    def public_status(self):
        current = self.current()
        return {
            "current": current.public(), "warning": self.warning,
            "options": [
                {"provider": provider, "model": option["model"], "label": option["label"],
                 "available": bool(environment_value(option["key_env"], self.project).strip())}
                for provider, option in MODEL_OPTIONS.items()
            ],
        }


_stores = {}
_stores_lock = RLock()
_request_config = ContextVar("request_llm_config", default=None)


def get_store(project=PROJECT_ROOT):
    project = Path(project).resolve()
    with _stores_lock:
        return _stores.setdefault(project, ModelSettingsStore(project))


def get_request_config():
    return _request_config.get() or get_store().current()


@contextmanager
def model_request():
    """처리 시작 때 모델을 선택하고 같은 요청 컨텍스트 안에서는 설정을 고정합니다.

    이 컨텍스트 안의 중첩 호출·재시도는 같은 설정을 사용합니다.
    컨텍스트가 종료된 뒤 새 요청을 시작하면 현재 저장된 설정을 다시 읽습니다.
    """
    existing = _request_config.get()
    config = existing or get_store().current()
    require_api_key(config)
    token = _request_config.set(config)
    try:
        if existing is None:
            LOGGER.info("LLM_MODEL %s", json.dumps(config.public(), ensure_ascii=False))
        yield config
    finally:
        _request_config.reset(token)
