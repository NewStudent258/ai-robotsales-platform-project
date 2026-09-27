from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.assistant import AssistantMessageRequest, AssistantMessageResponse
from app.services.assistant_service import AssistantService

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/messages", response_model=AssistantMessageResponse)
async def send_message(
    payload: AssistantMessageRequest, db: AsyncSession = Depends(get_db)
) -> AssistantMessageResponse:
    return await AssistantService(db).respond(payload.message, payload.session_id)
