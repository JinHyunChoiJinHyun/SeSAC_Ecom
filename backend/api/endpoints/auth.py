import os
import hmac

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status
)
from sqlalchemy.orm import Session

from backend.api.dependencies import get_operations_session
from backend.core.permissions import get_agent_scopes
from backend.core.security import create_agent_token
from backend.schemas.auth import (
    AgentTokenRequest,
    AgentTokenResponse
)
from backend.services.run_service import get_run_token_context

from backend.core.exceptions import (
    NotFoundError,
    ConflictError
)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

@router.post(
    "/agent-token",
    response_model=AgentTokenResponse
)
def issue_agent_token(
    request: AgentTokenRequest,
    session: Session = Depends(get_operations_session),
    service_key: str | None = Header(
        default=None,
        alias="X-Agent-Service-Key"
    )
):
    """Agent 시스템에 단기 JWT 발급"""

    # 1. Agent 시스템 서비스 자격 증명 검증
    expected_key = os.getenv("AGENT_SERVICE_KEY")

    if not expected_key or not service_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="서비스 인증 정보가 필요합니다."
        )

    if not hmac.compare_digest(service_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 서비스 인증 정보입니다."
        )

    # 2. DB에서 실제 Run 정보 조회 및 상태 검증
    try:
        run_context = get_run_token_context(session=session, run_id=request.run_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    # 3. Agent의 권한 계산
    try:
        scopes = get_agent_scopes(
            agent=request.agent,
            workflow=run_context["workflow"]
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="허용되지 않은 Agent 또는 Workflow입니다."
        )

    if not scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="해당 Workflow에서 허용된 권한이 없습니다."
        )

    # 4. JWT 생성
    token, expires_in = create_agent_token(
        agent=request.agent,
        run_id=request.run_id,
        scopes=scopes
    )

    return AgentTokenResponse(
        access_token=token,
        expires_in=expires_in
    )