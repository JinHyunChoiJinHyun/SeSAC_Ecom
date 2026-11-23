from fastapi import HTTPException, status
from functools import lru_cache
from pathlib import Path

import yaml

# 프로젝트 경로
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 권한 설정 파일 경로
PERMISSION_PATH = PROJECT_ROOT / "confing" / "permission.yaml"

@lru_cache(maxsize=1)
def load_permission_config() -> dict:
    """permissions.yaml 설정 파일 로드"""
    with PERMISSION_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError("권한 설정 파일 형식이 올바르지 않습니다.")

    return config

def get_agent_scope(agent: str, workflow: str) -> list[str]:
    """Agent와 Workflow의 공통 권한 계산"""
    config = load_permission_config()

    agents = config.get("agents", {})
    workflows = config.get("workflows", {})

    # Agent 기본 권한 조회
    agent_scopes = agents.get(agent)

    if not isinstance(agent_scopes, list):
        raise ValueError(f"등록되지 않은 Agent 입니다: {agent}")

    # Workflow 허용 권한 조회
    worflow_scopes = workflows.get(workflow)

    if not isinstance(worflow_scopes, list):
        raise ValueError(f"Workflow 권한 설정이 올바르지 않습니다: {workflow}")

    # 두 권한의 교집합 계산
    scopes = sorted(set(agent_scopes) & set(worflow_scopes))

    # 부여할 권한이 없는 경우 토큰 발급 차단
    if not scopes:
        raise ValueError(
            f"{workflow}에서 {agent}에게 허용된 권한이 없습니다."
        )

    return scopes

def check_permission(
        principal: dict,
        required_scope: str
) -> None:
    """Agent의 scope 보유 여부 검사"""

    # Agent 토큰인지 확인
    if principal.get("type") != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent만 접근할 수 있는 API입니다."
        )

    # scopes가 리스트인지 확인
    scopes = principal.get("scopes")

    if not isinstance(scopes, list):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="유효하지 않은 권한 정보입니다."
        )

    # 리스트 내부 요소 문자열 확인
    if not all(isinstance(scope, str) for scope in scopes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="유효하지 않은 권한 정보입니다."
        )

    # 정확히 일치하는 scope가 있는지 확인
    if required_scope not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"필요한 권한이 없습니다: {required_scope}"
        )