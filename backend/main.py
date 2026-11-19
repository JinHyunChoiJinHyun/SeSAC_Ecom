"""CartSignal FastAPI 애플리케이션"""

from fastapi import FastAPI

from backend.api.endpoints.chat import router as chat_router
from backend.api.endpoints.healthy import router as health_router
from backend.api.endpoints.product import router as product_router

app = FastAPI(
    title="CartSignal API",
    description="멀티 에이전트 마케팅 시스템 백엔드",
    version="0.1.0",
)

# 기존 Health API 등록
app.include_router(health_router)

# 멀티턴 Agent Chat API 등록
app.include_router(chat_router)

# Product API 등록
app.include_router(product_router)