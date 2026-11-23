# import os
# import hmac

# from fastapi import (
#     APIRouter,
#     Depends,
#     Header,
#     HTTPException,
#     status
# )
# from sqlalchemy.orm import Session

# from api.dependencies import get_operations_session
# from core.permissions import get_agent_scope
# from core.security import create_agent_token
# from schemas.auth import (
#     AgentTokenRequest,
#     AgentTokenResponse
# )
# from services

# router = APIRouter(
#     prefix="/auth",
#     tags=["Authentication"]
# )

# @router.get(
#     "/agent-token",
#     response_model=AgentTokenResponse
# )
# def issue_agent_token(
#     request: AgentTokenRequest,
#     session: Session = Depends(get_operations_session),
#     service_key: str | None = Header(
#         default=None,
#         alias="X-Agent-Service-Key"
#     )
# ):
#     """Agent 시스템에 단기 JWT 발급"""

#     # 1. Agent 시스템 서비스 자격 증명 검증
#     expected_key = os.getenv("AGENT_SERVICE_KEY")