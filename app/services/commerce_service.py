from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from hmac import compare_digest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import IdempotencyRecord, Order, OrderEvent, Quote, QuoteItem
from app.models.product import Product
from app.schemas.commerce import QuoteCreateRequest

ORDER_TRANSITIONS = {
    "CREATING": {"CREATED", "FAILED", "CANCELLED"},
    "CREATED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"COMPLETED", "FAILED", "CANCELLED"},
    "FAILED": {"CREATING", "CANCELLED"},
}


def _number(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:6].upper()}"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CommerceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_quote(self, payload: QuoteCreateRequest) -> Quote:
        product_ids = [item.product_id for item in payload.items]
        products = list(
            await self.session.scalars(
                select(Product).where(Product.id.in_(product_ids), Product.is_active.is_(True))
            )
        )
        by_id = {product.id: product for product in products}
        if len(by_id) != len(set(product_ids)):
            raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND"})
        if len({product.currency for product in products}) != 1:
            raise HTTPException(status_code=422, detail={"code": "MIXED_CURRENCY"})
        subtotal = Decimal("0")
        item_rows: list[QuoteItem] = []
        for item in payload.items:
            product = by_id[item.product_id]
            line_total = (product.base_price * item.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            subtotal += line_total
            item_rows.append(
                QuoteItem(
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.base_price,
                    quantity=item.quantity,
                    line_total=line_total,
                )
            )
        tax = Decimal("0.00")
        quote = Quote(
            quote_number=_number("Q"),
            access_token=uuid4().hex + uuid4().hex,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            currency=products[0].currency,
            subtotal=subtotal,
            tax=tax,
            total=subtotal + tax,
            status="PENDING_CONFIRMATION",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            snapshot={"product_ids": product_ids, "pricing_policy": "base-price-v1"},
        )
        self.session.add(quote)
        await self.session.flush()
        for item in item_rows:
            item.quote_id = quote.id
            self.session.add(item)
        await self.session.commit()
        await self.session.refresh(quote)
        return quote

    async def get_quote(self, quote_id: int) -> tuple[Quote, list[QuoteItem]] | None:
        quote = await self.session.get(Quote, quote_id)
        if quote is None:
            return None
        items = list(await self.session.scalars(select(QuoteItem).where(QuoteItem.quote_id == quote_id)))
        return quote, items

    async def require_quote_access(self, quote_id: int, access_token: str) -> Quote:
        quote = await self.session.get(Quote, quote_id)
        if quote is None or not compare_digest(quote.access_token, access_token):
            raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
        return quote

    async def confirm_quote(self, quote_id: int, version: int) -> Quote:
        quote = await self.session.get(Quote, quote_id, with_for_update=True)
        if quote is None:
            raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
        if quote.version != version or quote.status != "PENDING_CONFIRMATION":
            raise HTTPException(status_code=409, detail={"code": "QUOTE_VERSION_CONFLICT"})
        if _utc(quote.expires_at) and _utc(quote.expires_at) <= datetime.now(timezone.utc):
            quote.status = "EXPIRED"
            await self.session.commit()
            raise HTTPException(status_code=409, detail={"code": "QUOTE_EXPIRED"})
        quote.status = "CONFIRMED"
        await self.session.commit()
        await self.session.refresh(quote)
        return quote

    async def create_order(self, quote_id: int, idempotency_key: str) -> Order:
        request_hash = sha256(str(quote_id).encode()).hexdigest()
        existing = await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.operation == "create_order",
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
            order = await self.session.get(Order, existing.response.get("order_id"))
            if order:
                return order
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_RESULT_MISSING"})

        quote = await self.session.get(Quote, quote_id, with_for_update=True)
        if quote is None:
            raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
        if quote.status != "CONFIRMED":
            raise HTTPException(status_code=409, detail={"code": "QUOTE_NOT_CONFIRMED"})
        order = Order(
            order_number=_number("O"),
            quote_id=quote.id,
            customer_name=quote.customer_name,
            customer_email=quote.customer_email,
            total=quote.total,
            status="CREATING",
        )
        self.session.add(order)
        await self.session.flush()
        self.session.add(OrderEvent(order_id=order.id, from_status=None, to_status="CREATING"))
        self.session.add(
            IdempotencyRecord(
                operation="create_order",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response={"order_id": order.id},
            )
        )
        quote.status = "ORDER_CREATED"
        order.status = "CREATED"
        self.session.add(OrderEvent(order_id=order.id, from_status="CREATING", to_status="CREATED"))
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def transition_order(self, order_id: int, to_status: str, reason: str | None = None) -> Order:
        order = await self.session.get(Order, order_id, with_for_update=True)
        if order is None:
            raise HTTPException(status_code=404, detail={"code": "ORDER_NOT_FOUND"})
        if to_status not in ORDER_TRANSITIONS.get(order.status, set()):
            raise HTTPException(status_code=409, detail={"code": "ORDER_INVALID_TRANSITION"})
        previous = order.status
        order.status = to_status
        self.session.add(OrderEvent(order_id=order.id, from_status=previous, to_status=to_status, reason=reason))
        await self.session.commit()
        await self.session.refresh(order)
        return order
