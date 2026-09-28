from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.commerce import Order
from app.schemas.commerce import OrderRead, QuoteCreateRequest, QuoteItemRead, QuoteRead
from app.services.commerce_service import CommerceService

router = APIRouter(tags=["commerce"])


def _quote_read(quote_tuple) -> QuoteRead:
    quote, items = quote_tuple
    return QuoteRead(
        id=quote.id,
        quote_number=quote.quote_number,
        access_token=quote.access_token,
        customer_name=quote.customer_name,
        customer_email=quote.customer_email,
        currency=quote.currency,
        subtotal=quote.subtotal,
        tax=quote.tax,
        total=quote.total,
        status=quote.status,
        version=quote.version,
        expires_at=quote.expires_at,
        items=[
            QuoteItemRead(
                product_id=item.product_id,
                product_name=item.product_name,
                unit_price=item.unit_price,
                quantity=item.quantity,
                line_total=item.line_total,
            )
            for item in items
        ],
    )


async def _load_quote(service: CommerceService, quote_id: int) -> QuoteRead:
    result = await service.get_quote(quote_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
    return _quote_read(result)


@router.post("/quotes", response_model=QuoteRead, status_code=201)
async def create_quote(payload: QuoteCreateRequest, db: AsyncSession = Depends(get_db)) -> QuoteRead:
    service = CommerceService(db)
    quote = await service.create_quote(payload)
    return await _load_quote(service, quote.id)


@router.get("/quotes/{quote_id}", response_model=QuoteRead)
async def get_quote(
    quote_id: int,
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> QuoteRead:
    service = CommerceService(db)
    await service.require_quote_access(quote_id, access_token)
    return await _load_quote(service, quote_id)


@router.post("/quotes/{quote_id}/confirm", response_model=QuoteRead)
async def confirm_quote(
    quote_id: int,
    version: int,
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> QuoteRead:
    service = CommerceService(db)
    await service.require_quote_access(quote_id, access_token)
    await service.confirm_quote(quote_id, version)
    return await _load_quote(service, quote_id)


@router.post("/orders", response_model=OrderRead, status_code=201)
async def create_order(
    quote_id: int,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> OrderRead:
    service = CommerceService(db)
    await service.require_quote_access(quote_id, access_token)
    return await service.create_order(quote_id, idempotency_key)


@router.get("/orders/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: int,
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> OrderRead:
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail={"code": "ORDER_NOT_FOUND"})
    await CommerceService(db).require_quote_access(order.quote_id, access_token)
    return order
