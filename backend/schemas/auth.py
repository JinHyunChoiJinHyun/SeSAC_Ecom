from pydantic import BaseModel, Field
from typing import Literal

class AgentTokenRequest(BaseModel):
    run_id: str = Field(min_length=1)
    agent: Literal[
        "orchestrator",
        "market",
        "product",
        "targeting",
        "content",
        "validation",
        "reporting",
        "execution"
    ]

class AgentTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int