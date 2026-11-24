import os
from datetime import datetime, timedelta, timezone

from jose import jwt

from uuid import UUID

def create_agent_token(
        agent: str,
        run_id: UUID,
        scopes: list[str]
) -> tuple[str, int]:
    """Agent 전용 JWT 생성"""

    secret = os.environ["AGENT_JWT_SECRET"]
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")

    expires_in = 15 * 60

    now = datetime.now(timezone.utc)

    payload = {
        "type": "agent",
        "agent": agent,
        "run_id": run_id,
        "scopes": scopes,
        "iat": now, # issued at
        "exp": now + timedelta(seconds=expires_in), # expiration time
    }

    token = jwt.encode(
        payload,
        secret,
        algorithm=algorithm
    )

    return token, expires_in