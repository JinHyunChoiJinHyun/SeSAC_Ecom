from fastapi import FastAPI
from backend.api.endpoints.healthy import router as health_router

app = FastAPI(
    title="CartSignal API",
    description="멀티 에이전트 마케팅 시스템 백엔드",
    version="0.1.0"
)

# Health Check 라우터 등록
app.include_router(health_router)