"""FastAPI 의존성 주입"""
import os

from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from database.operations.connection import get_session_factory
from backend.core.permissions import check_permission

# HTTP 요청의 Authorization 헤더에서 Bearer 토큰을 추출하는 인증 스키마 
bearer_scheme = HTTPBearer(auto_error=False) # auto_error=False: 토큰이 없을 경우 자동으로 예외 발생이 아닌 None 반환하도록 설정

def get_chat_service():
    """멀티턴 Agent 서비스 제공"""
    from backend.services.chat_service import get_chat_service as _get
    return _get()

def get_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)
) -> dict:
    """요청에 포함된 JWT 토큰 검증 및 사용자 정보 반환"""

    # Authorization 헤더 확인
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다."
        )

    token = credentials.credentials

    # Agent와 User의 서명 키 분리
    token_secrets = {
        "agent": os.getenv("AGENT_JWT_SECRET"),
        "user": os.getenv("USER_JWT_SECRET")
    }

    # 필수 환경변수 확인
    if not all(token_secrets.values()):
        raise RuntimeError("JWT 서명 키가 설정되지 않았습니다.")

    algorithm = os.getenv("JWT_ALGORITHM", "HS256")

    # 각 서명 키로 토큰 검증
    principal = None

    for token_type, secret in token_secrets.items():
        try:
            decoded = jwt.decode(
                token,
                secret,
                algorithms=[algorithm],
                options={"require_exp": True}
            )
        except JWTError:
            continue

        # 검증에 사용한 키와 토큰 유형이 같아야 통과
        if decoded.get("type") == token_type:
            principal = decoded
            break

    # 서명 또는 만료 시간 검증 실패
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 토큰입니다."
        )

    # Agent 필수 토큰 정보 검증
    if principal.get("type") == "agent":
        required_claims = ("agent", "run_id", "scopes")

        if not all(principal.get(key) is not None for key in required_claims):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="필수 토큰 정보가 누락되었습니다."
            )

    return principal

def get_operations_session() -> Generator[Session, None, None]:
    """PostgreSQL 작업용 Session 제공 (트랜잭션 관리는 Service에서 담당)"""
    session = get_session_factory()()

    try:
        yield session

    except Exception:
        # 예외 발생 시 트랜젝션 롤백
        session.rollback()
        raise

    finally:
        session.close()

def require_scope(required_scope: str):
    """Agent의 API 호출 권한(scope) 검사"""

    # 외부 함수의 입력 파라미터를 기억하고 내부에서 사용하기 위해 내부 함수 구현
    def check_scope(
            principal: dict = Depends(get_principal)
    ):
        # Agent 토큰인지 확인
        if principal.get("type") != "agent":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent만 접근할 수 있는 API입니다."
            )

        # 권한 보유 여부 확인
        check_permission(
            principal=principal,
            required_scope=required_scope
        )

        return principal

    return check_scope

