import asyncio
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from hmac import compare_digest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import IdempotencyRecord, Order, OrderEvent, Quote, QuoteItem
from app.models.product import Product
from app.schemas.commerce import QuoteCreateRequest
from app.services.pricing_service import PriceLine, PriceResult, PricingService

# 报价有效期（天）。创建与重新报价共用，避免两处漂移。
QUOTE_VALIDITY_DAYS = 7

# ARCH.md §3 有限状态机：终态出度为 0，必须显式列出以免 setdefault 回退为空集。
ORDER_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_CONFIRMATION", "EXPIRED"},
    "PENDING_CONFIRMATION": {"CONFIRMED", "EXPIRED", "DRAFT"},
    "CONFIRMED": {"CREATING", "CANCELLED"},
    "CREATING": {"CREATED", "FAILED", "CANCELLED"},
    "CREATED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"COMPLETED", "FAILED", "CANCELLED"},
    "FAILED": {"CREATING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
}

# 允许从这些报价状态创建订单：已确认，或此前已为该报价成功建单（幂等重放）。
ORDERABLE_QUOTE_STATUSES = {"CONFIRMED", "ORDER_CREATED"}

# 不允许被重新报价的状态：已建单/被取代/已过期的报价必须保持原样，
# 否则已成交或已失效的金额会被静默改写，审计链断裂。
NON_REVISABLE_STATUSES = {"ORDER_CREATED", "SUPERSEDED", "EXPIRED"}


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
        products = await self._load_products(payload)
        by_id = {product.id: product for product in products}

        lines: list[PriceLine] = []
        for item in payload.items:
            product = by_id[item.product_id]
            line_total = (product.base_price * item.quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            lines.append(
                PriceLine(
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.base_price,
                    quantity=item.quantity,
                    line_total=line_total,
                )
            )

        pricing = PricingService(self.session)
        rules = await pricing.load_active_rules()
        result = pricing.evaluate(
            rules,
            lines,
            context={"currency": products[0].currency, "industry": payload.industry},
        )

        quote = Quote(
            quote_number=_number("Q"),
            access_token=uuid4().hex + uuid4().hex,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            currency=result.currency,
            subtotal=result.subtotal,
            discount=result.discount,
            tax=result.tax,
            total=result.total,
            status="PENDING_CONFIRMATION",
            version=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=QUOTE_VALIDITY_DAYS),
            snapshot=self._build_snapshot(result, payload),
        )
        self.session.add(quote)
        await self.session.flush()
        # 首版自指，使 root_quote_id 对首版同样可查（避免 NULL 分支判断）。
        quote.root_quote_id = quote.id
        for line in result.lines:
            self.session.add(
                QuoteItem(
                    quote_id=quote.id,
                    product_id=line.product_id,
                    product_name=line.product_name,
                    unit_price=line.unit_price,
                    quantity=line.quantity,
                    line_total=line.line_total,
                )
            )
        await self.session.commit()
        await self.session.refresh(quote)
        return quote

    async def _load_products(self, payload: QuoteCreateRequest) -> list[Product]:
        """加载并校验报价涉及的产品。价格一律取自服务端目录。"""
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
        return products

    @staticmethod
    def _build_snapshot(result: PriceResult, payload: QuoteCreateRequest) -> dict:
        """固化定价依据：规则版本与逐行金额，保证报价可复算、可审计。"""
        snapshot = result.snapshot()
        snapshot["product_ids"] = [item.product_id for item in payload.items]
        snapshot["items"] = [
            {
                "product_id": line.product_id,
                "unit_price": str(line.unit_price),
                "quantity": line.quantity,
                "line_total": str(line.line_total),
            }
            for line in result.lines
        ]
        snapshot["industry"] = payload.industry
        return snapshot

    async def get_quote(self, quote_id: int) -> tuple[Quote, list[QuoteItem]] | None:
        quote = await self.session.get(Quote, quote_id)
        if quote is None:
            return None
        items = list(
            await self.session.scalars(select(QuoteItem).where(QuoteItem.quote_id == quote_id))
        )
        return quote, items

    async def get_quote_history(self, quote_id: int) -> list[Quote]:
        """返回同一报价链上的全部版本，按版本号升序。

        链以 `root_quote_id` 聚合：首版自指，后续版本指向首版。
        """
        quote = await self.session.get(Quote, quote_id)
        if quote is None:
            return []
        root_id = quote.root_quote_id or quote.id
        versions = list(
            await self.session.scalars(
                select(Quote)
                .where((Quote.id == root_id) | (Quote.root_quote_id == root_id))
                .order_by(Quote.version.asc(), Quote.id.asc())
            )
        )
        return versions

    async def revise_quote(
        self, quote_id: int, payload: QuoteCreateRequest, expected_version: int
    ) -> Quote:
        """基于既有报价生成新版本。

        P1 语义（已确认）：**新建一条 Quote 行**并通过 `root_quote_id` 关联，
        旧报价标记 `SUPERSEDED`。不就地改写原报价——否则已确认报价的
        审计链会断裂，且历史金额不可追溯。

        `expected_version` 为乐观锁：调用方回传其读到的版本号，
        与库中不一致时拒绝，避免基于过期视图覆盖他人修改。
        """
        original = await self.session.get(Quote, quote_id, with_for_update=True)
        if original is None:
            raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
        if original.version != expected_version:
            raise HTTPException(status_code=409, detail={"code": "QUOTE_VERSION_CONFLICT"})
        if original.status in NON_REVISABLE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "QUOTE_NOT_REVISABLE"})

        products = await self._load_products(payload)
        by_id = {product.id: product for product in products}
        # 重新报价必须保持币种一致，否则新旧版本不可比。
        if products[0].currency != original.currency:
            raise HTTPException(status_code=422, detail={"code": "MIXED_CURRENCY"})

        lines: list[PriceLine] = []
        for item in payload.items:
            product = by_id[item.product_id]
            line_total = (product.base_price * item.quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            lines.append(
                PriceLine(
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.base_price,
                    quantity=item.quantity,
                    line_total=line_total,
                )
            )

        pricing = PricingService(self.session)
        rules = await pricing.load_active_rules()
        result = pricing.evaluate(
            rules,
            lines,
            context={"currency": products[0].currency, "industry": payload.industry},
        )

        root_id = original.root_quote_id or original.id
        # 版本号取链上最大值 +1，而不是 original.version + 1：
        # 若曾出现并发改写，前者能避免版本号撞车。
        chain = await self.get_quote_history(quote_id)
        next_version = max((row.version for row in chain), default=original.version) + 1

        revision = Quote(
            quote_number=_number("Q"),
            access_token=uuid4().hex + uuid4().hex,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            currency=result.currency,
            subtotal=result.subtotal,
            discount=result.discount,
            tax=result.tax,
            total=result.total,
            status="PENDING_CONFIRMATION",
            version=next_version,
            root_quote_id=root_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=QUOTE_VALIDITY_DAYS),
            snapshot=self._build_snapshot(result, payload) | {"revised_from": original.id},
        )
        self.session.add(revision)
        await self.session.flush()
        for line in result.lines:
            self.session.add(
                QuoteItem(
                    quote_id=revision.id,
                    product_id=line.product_id,
                    product_name=line.product_name,
                    unit_price=line.unit_price,
                    quantity=line.quantity,
                    line_total=line.line_total,
                )
            )
        # 旧版标记被取代，保留其金额与状态用于审计。
        original.status = "SUPERSEDED"
        original.superseded_by_id = revision.id
        await self.session.commit()
        await self.session.refresh(revision)
        return revision

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

    async def _find_idempotent_order(self, idempotency_key: str, request_hash: str) -> Order | None:
        """返回已存在的幂等结果；键被复用于其它请求时抛出冲突。"""
        existing = await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.operation == "create_order",
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
        order_id = (existing.response or {}).get("order_id")
        if order_id is None:
            # 键已被抢占但结果尚未写入：调用方需等待或重试。
            return None
        order = await self.session.get(Order, order_id)
        if order is None:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_RESULT_MISSING"})
        return order

    async def _claim_idempotency_key(self, idempotency_key: str, request_hash: str) -> bool:
        """尝试独占该幂等键。

        在独立事务中提交，因此并发的落败方回滚的只是自己这次抢占，
        不会连带撤销胜出方已提交的订单。返回 True 表示本次请求获得执行权。
        """
        self.session.add(
            IdempotencyRecord(
                operation="create_order",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response={},
            )
        )
        try:
            await self.session.commit()
            return True
        except IntegrityError:
            await self.session.rollback()
            # 抢占失败：可能是同键并发，也可能是该键被复用于别的报价。
            await self._find_idempotent_order(idempotency_key, request_hash)
            return False

    async def create_order(self, quote_id: int, idempotency_key: str) -> Order:
        request_hash = sha256(f"create_order:{quote_id}".encode()).hexdigest()

        replayed = await self._find_idempotent_order(idempotency_key, request_hash)
        if replayed is not None:
            return replayed

        quote = await self.session.get(Quote, quote_id, with_for_update=True)
        if quote is None:
            raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
        if quote.status not in ORDERABLE_QUOTE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "QUOTE_NOT_CONFIRMED"})
        # 报价过期后不得再建单：确认路径已校验，下单路径此前漏检，会造成过期报价成交。
        expires_at = _utc(quote.expires_at)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            quote.status = "EXPIRED"
            await self.session.commit()
            raise HTTPException(status_code=409, detail={"code": "QUOTE_EXPIRED"})

        if not await self._claim_idempotency_key(idempotency_key, request_hash):
            # 另一个并发请求正在建单：等待其提交后回放结果，而不是重复建单。
            for _ in range(10):
                replayed = await self._find_idempotent_order(idempotency_key, request_hash)
                if replayed is not None:
                    return replayed
                await asyncio.sleep(0.05)
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS"})

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
        quote.status = "ORDER_CREATED"
        order.status = "CREATED"
        self.session.add(OrderEvent(order_id=order.id, from_status="CREATING", to_status="CREATED"))
        record = await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.operation == "create_order",
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        record.response = {"order_id": order.id}
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def transition_order(
        self,
        order_id: int,
        to_status: str,
        reason: str | None = None,
        actor: str = "system",
        expected_status: str | None = None,
    ) -> Order:
        """按状态机迁移订单状态。

        `expected_status` 提供乐观并发保护：调用方回传其读到的状态，
        与库中不一致时拒绝，避免基于过期读到的状态做出错误迁移。
        """
        order = await self.session.get(Order, order_id, with_for_update=True)
        if order is None:
            raise HTTPException(status_code=404, detail={"code": "ORDER_NOT_FOUND"})
        if expected_status is not None and order.status != expected_status:
            raise HTTPException(status_code=409, detail={"code": "ORDER_STATUS_CONFLICT"})
        if to_status not in ORDER_TRANSITIONS.get(order.status, set()):
            raise HTTPException(status_code=409, detail={"code": "ORDER_INVALID_TRANSITION"})
        previous = order.status
        order.status = to_status
        self.session.add(
            OrderEvent(
                order_id=order.id,
                from_status=previous,
                to_status=to_status,
                reason=reason,
                actor=actor,
            )
        )
        await self.session.commit()
        await self.session.refresh(order)
        return order
