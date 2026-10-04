from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.assistant import (
    AssistantMessageRequest,
    AssistantMessageResponse,
    QuoteTokenRequest,
    QuoteTokenResponse,
)
from app.services.assistant_service import AssistantService

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/messages", response_model=AssistantMessageResponse)
async def send_message(
    payload: AssistantMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AssistantMessageResponse:
    return await AssistantService(db).respond(
        payload.message, payload.session_id, trace_id=getattr(request.state, "trace_id", None)
    )


@router.post("/quote-token", response_model=QuoteTokenResponse)
async def issue_quote_token(
    payload: QuoteTokenRequest, db: AsyncSession = Depends(get_db)
) -> QuoteTokenResponse:
    """为会话内生成的报价签发访问令牌。

    前端在用户点击「确认报价」时才调用本端点，令牌因此不随对话响应
    批量回流。仅当该报价确实由本会话生成时才会签发。
    """
    token = await AssistantService(db).issue_quote_token(payload.session_id, payload.quote_id)
    if token is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
    return QuoteTokenResponse(quote_id=payload.quote_id, access_token=token)
