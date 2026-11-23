from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.dependencies import (
    get_operations_session,
    require_scope
)

from backend.schemas.knowledge import(
    KnowledgeSearchRequest,
    KnowledgeSearchResponse
)

from backend.services.knowledge_service import(
    search_business_knowledge
)

router = APIRouter(
    prefix="/knowledge",
    tags=["Knowledge"]
)

@router.post(
    "/search",
    response_model=KnowledgeSearchResponse,
)
def search_knowledge(
    request: KnowledgeSearchRequest,
    session: Session = Depends(get_operations_session),
    # _principal: dict = Depends(require_scope("knowledge:read"))
):
    return search_business_knowledge(
        session=session,
        query=request.query,
        knowledge_type=request.knowledge_type,
        category=request.category,
        top_k=request.top_k
    )