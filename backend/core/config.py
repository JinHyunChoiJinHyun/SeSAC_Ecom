from datetime import date
from pathlib import Path

from pydantic import model_validator, SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # H&M 데이터 기간
    HM_DATA_START: date = date(2018, 9, 20)
    HM_DATA_END: date = date(2020, 9, 22)
    HM_DATA_REFERENCE_DATE: date = date(2020, 9, 22) # 기준 날짜

    # 파일 경로
    ANALYTICS_DB_PATH: Path = BASE_DIR / "data" / "analytics.duckdb"
    # HM_VOCAB_PATH: Path = BASE_DIR / "config" / "hm_vocab.json" 필요 시 구현
    PERMISSIONS_PATH: Path = BASE_DIR / "config" / "permissions.yaml" # 에이전트 권한 설정

    # PostgreSQL
    OPERATIONS_DB_URL: str
    ANALYTICS_LOCK_ID: int = 7001

    # 토큰 (사람/Agent 서명 키 분리)
    USER_TOKEN_SECRET: SecretStr = Field(min_length=32)
    AGENT_TOKEN_SECRET: SecretStr = Field(min_length=32)
    AGENT_TOKEN_TTL_MINUTES: int = 15 # 토큰 유효 시간

    @model_validator(mode="after")
    def validate_analysis_period(self):
        if not self.HM_DATA_START <= self.HM_DATA_REFERENCE_DATE <= self.HM_DATA_END:
            raise ValueError(
                "HM_DATA_REFERENCE_DATE H&M 데이터 기간 내에 있어야 합니다."
            )

        if self.USER_TOKEN_SECRET.get_secret_value() == self.AGENT_TOKEN_SECRET.get_secret_value():
            raise ValueError("USER_TOKEN_SECRET과 AGENT_TOKEN_SECRET은 서로 달라야 합니다")

settings = Settings()