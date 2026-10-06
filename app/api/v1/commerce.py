from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import require_admin
from app.db.session import get_db
from app.models.commerce import Order
from app.schemas.commerce import (
    AppliedRuleRead,
    OrderRead,
    OrderTransitionRequest,
    QuoteCreateRequest,
    QuoteItemRead,
    QuoteRead,
    QuoteReviseRequest,
    QuoteVersionRead,
)
from app.services.commerce_service import ORDER_TRANSITIONS, CommerceService

router = APIRouter(tags=["commerce"])


def _rule_detail(rule: dict) -> str:
    label = "折扣" if rule.get("kind") == "discount" else "税费"
    return f"{rule.get('name', '')}（{label}）"


def _applied_rules(quote) -> list[AppliedRuleRead]:
    """从报价快照还原生效规则，使金额可解释、可追溯。"""
    snapshot = quote.snapshot or {}
    return [
        AppliedRuleRead(
            code=str(rule.get("code", "")),
            name=str(rule.get("name", "")),
            kind=str(rule.get("kind", "")),
            version=int(rule.get("version", 1)),
            priority=int(rule.get("priority", 0)),
            amount=Decimal(str(rule.get("amount", "0"))),
            detail=_rule_detail(rule),
        )
        for rule in snapshot.get("rules", [])
    ]


def _quote_read(quote_tuple, *, include_token: bool, versions: list | None = None) -> QuoteRead:
    quote, items = quote_tuple
    # 访问令牌是读取/确认该报价的凭证，默认只在创建时返回一次，
    # 避免它随每次读取回流到响应体、日志或浏览器缓存。
    token = quote.access_token if (include_token or get_settings().expose_quote_token) else None
    return QuoteRead(
        id=quote.id,
        quote_number=quote.quote_number,
        access_token=token,
        customer_name=quote.customer_name,
        customer_email=quote.customer_email,
        currency=quote.currency,
        subtotal=quote.subtotal,
        discount=quote.discount,
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
        applied_rules=_applied_rules(quote),
        versions=[
            QuoteVersionRead(
                id=row.id,
                quote_number=row.quote_number,
                version=row.version,
                status=row.status,
                subtotal=row.subtotal,
                discount=row.discount,
                tax=row.tax,
                total=row.total,
                is_current=row.id == quote.id,
            )
            for row in (versions or [])
        ],
    )


async def _load_quote(
    service: CommerceService, quote_id: int, *, include_token: bool, with_versions: bool = True
) -> QuoteRead:
    result = await service.get_quote(quote_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND"})
    chain = await service.get_quote_history(quote_id) if with_versions else []
    return _quote_read(result, include_token=include_token, versions=chain)


@router.post("/quotes", response_model=QuoteRead, status_code=201)
async def create_quote(
    payload: QuoteCreateRequest, db: AsyncSession = Depends(get_db)
) -> QuoteRead:
    service = CommerceService(db)
    quote = await service.create_quote(payload)
    # 创建响应是客户获得访问令牌的唯一时机。
    return await _load_quote(service, quote.id, include_token=True)


@router.get("/quotes/{quote_id}", response_model=QuoteRead)
async def get_quote(
    quote_id: int,
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> QuoteRead:
    service = CommerceService(db)
    await service.require_quote_access(quote_id, access_token)
    # 调用方已持有该令牌，读取响应无需回显。
    return await _load_quote(service, quote_id, include_token=False)


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
    return await _load_quote(service, quote_id, include_token=False)


@router.post("/quotes/{quote_id}/revise", response_model=QuoteRead, status_code=201)
async def revise_quote(
    quote_id: int,
    payload: QuoteReviseRequest,
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> QuoteRead:
    """基于既有报价生成新版本。

    客户修改数量、方案或联系信息时调用；旧版本标记 `SUPERSEDED` 并保留原金额，
    新版本成为唯一可确认的当前版本。`expected_version` 提供乐观并发保护。
    响应返回新版本，其中 `versions` 给出完整版本链。
    """
    service = CommerceService(db)
    await service.require_quote_access(quote_id, access_token)
    revision = await service.revise_quote(quote_id, payload, payload.expected_version)
    return await _load_quote(service, revision.id, include_token=True)


def _order_read(order: Order) -> OrderRead:
    """序列化订单，并附带当前状态下的合法迁移目标。"""
    return OrderRead(
        id=order.id,
        order_number=order.order_number,
        quote_id=order.quote_id,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        total=order.total,
        status=order.status,
        allowed_transitions=sorted(ORDER_TRANSITIONS.get(order.status, set())),
    )


@router.post("/orders", response_model=OrderRead, status_code=201)
async def create_order(
    quote_id: int,
    idempotency_key: str = Header(min_length=8, alias="Idempotency-Key"),
    access_token: str = Header(alias="X-Quote-Token"),
    db: AsyncSession = Depends(get_db),
) -> OrderRead:
    service = CommerceService(db)
    await service.require_quote_access(quote_id, access_token)
    return _order_read(await service.create_order(quote_id, idempotency_key))


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
    return _order_read(order)


@router.post("/orders/{order_id}/transition", response_model=OrderRead)
async def transition_order(
    order_id: int,
    payload: OrderTransitionRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_admin),
) -> OrderRead:
    """运营端订单状态迁移。

    属于高风险写操作（取消订单、履约推进），需后台令牌；
    状态机是唯一裁决者，非法迁移一律拒绝。
    """
    order = await CommerceService(db).transition_order(
        order_id,
        payload.to_status,
        reason=payload.reason,
        actor=actor,
        expected_status=payload.expected_status,
    )
    return _order_read(order)
