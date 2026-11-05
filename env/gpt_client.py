from dotenv import load_dotenv
import os
from pathlib import Path
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings, ChatOpenAI

# OpenAI 기본 클라이언트 생성 함수
def get_gpt_client():
    try:
        load_dotenv()
        client = OpenAI(api_key = os.getenv("OPENAI_API_KEY"))
    except:
        client = None
    return client

# LangChain 채팅 모델 클라이언트 생성 함수
def get_openai_chat_client(model=None, *, model_config=None):
    """요청에 고정된 제공 업체로 채팅 클라이언트를 생성합니다."""
    # 기존 OpenAI 조직·프록시 등 환경변수 설정도 절대 경로에서 로드합니다.
    load_dotenv(Path(__file__).with_name(".env"))
    from env.model_settings import (
        get_request_config, validate_selection, require_api_key,
        environment_value, ModelSettingsError,
    )
    config = validate_selection(model_config) if model_config is not None else get_request_config()
    if model is not None and model != config.model:
        raise ModelSettingsError("명시한 모델이 이 요청의 모델 설정과 다릅니다.")
    options = {"api_key": require_api_key(config), "model": config.model}
    if config.provider == "nvidia":
        options.update(
            base_url="https://integrate.api.nvidia.com/v1",
            reasoning_effort="none", max_tokens=4096, timeout=90, max_retries=1,
        )
    elif config.provider == "gemini":
        # Gemini의 OpenAI 호환 API를 사용하며 NVIDIA 전용 추론 옵션은 전달하지 않습니다.
        # 한도 오류를 자동 재호출하지 않고 기존 요청 실패 처리로 전달합니다.
        options.update(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=90, max_retries=0,
        )
    else:
        # GPT는 기존 OPENAI 설정과 SDK의 기본 API 주소를 유지합니다.
        base_url = environment_value("OPENAI_API_BASE") or environment_value("OPENAI_BASE_URL")
        if base_url:
            options["base_url"] = base_url
    return ChatOpenAI(**options)

# OpenAI 임베딩 모델 클라이언트 생성 함수
def get_openai_embedding_client():
    try:
        load_dotenv()
        client = OpenAIEmbeddings(api_key = os.getenv("OPENAI_API_KEY"),
                                  model = 'text-embedding-3-small')
    except:
        client = None
    return client
