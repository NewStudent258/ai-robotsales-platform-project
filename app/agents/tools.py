"""注册给 Agent 的受控工具集。

分层原则（ARCH.md §1、AGENTS.md §5）：
- `search_products` 只读目录，无副作用；
- `create_quote` 复用 `CommerceService.create_quote`，金额由服务端复算；
- `create_order` 属 `write_commit`，**不真正执行**，只回传待确认动作，
  由客户在页面上核对条款后提交，避免模型自主下单。

任何工具都不得自行计算金额或直接写库：它们只做参数转译与结果裁剪。
"""

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.provider import (
    TOOL_RISK_READ,
    TOOL_RISK_WRITE_COMMIT,
    TOOL_RISK_WRITE_LOCAL,
    AgentContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from app.schemas.commerce import QuoteCreateRequest
from app.services.commerce_service import ORDERABLE_QUOTE_STATUSES, CommerceService
from app.services.product_service import ProductService

# 助手检索结果上限，避免把整个目录塞进上下文。
SEARCH_LIMIT = 3


async def _search_products(
    session: AsyncSession, arguments: dict[str, Any], context: AgentContext
) -> ToolResult:
    """按自然语言关键词检索候选产品。只读。

    检索词优先取已累积的结构化需求（场景）加当前消息：仅用最新一句会让
    「预算50万」这类补充信息把先前说过的场景挤出上下文，导致候选丢失。
    """
    requirement = context.requirement or {}
    parts = [
        str(requirement.get("use_case") or ""),
        str(requirement.get("industry") or ""),
        str(arguments.get("query") or ""),
    ]
    query = " ".join(part for part in parts if part).strip() or context.message
    products = await ProductService(session).search_for_assistant(query, limit=SEARCH_LIMIT)
    return ToolResult(
        name="search_products",
        ok=True,
        data={
            "count": len(products),
            "products": [
                {
                    "product_id": product.id,
                    "name": product.name,
                    "base_price": str(product.base_price),
                    "currency": product.currency,
                    "use_cases": list(product.use_cases or []),
                }
                for product in products
            ],
        },
        message=None if products else "未检索到匹配产品。",
    )


async def _create_quote(
    session: AsyncSession, arguments: dict[str, Any], context: AgentContext
) -> ToolResult:
    """为客户需求生成正式报价草稿。

    金额一律由 `CommerceService` 依据 `Product.base_price` 复算；
    本函数只负责把参数整理成 `QuoteCreateRequest` 并校验。
    """
    try:
        payload = QuoteCreateRequest(
            customer_name=str(arguments.get("customer_name") or "").strip(),
            customer_email=str(arguments.get("customer_email") or "").strip(),
            items=[
                {
                    "product_id": int(arguments["product_id"]),
                    "quantity": int(arguments.get("quantity", 1)),
                }
            ],
            # 行业来自已抽取的结构化需求，仅供规则匹配；金额仍由价格服务决定。
            industry=context.requirement.get("industry"),
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        # 缺少联系人等必填信息时，不生成报价，改为向用户追问。
        return ToolResult(
            name="create_quote",
            ok=False,
            error_code="QUOTE_ARGUMENTS_INVALID",
            message="生成报价需要产品、数量、联系人和邮箱，请先补充这些信息。",
        )

    quote = await CommerceService(session).create_quote(payload)
    return ToolResult(
        name="create_quote",
        ok=True,
        data={
            "quote_id": quote.id,
            "quote_number": quote.quote_number,
            # 访问令牌是客户读取/确认该报价的凭证，随本响应回传一次。
            "access_token": quote.access_token,
            "currency": quote.currency,
            "subtotal": str(quote.subtotal),
            # 折扣与税额由价格服务按规则计算，Agent 只做透传与解释。
            "discount": str(quote.discount),
            "tax": str(quote.tax),
            "total": str(quote.total),
            "version": quote.version,
            "expires_at": quote.expires_at.isoformat() if quote.expires_at else None,
        },
    )


async def _prepare_order(
    session: AsyncSession, arguments: dict[str, Any], context: AgentContext
) -> ToolResult:
    """准备下单动作，但**不创建订单**。

    下单属业务承诺：按 AGENTS.md §5「写工具默认需要明确用户确认」，
    这里只校验报价可下单性并返回待确认动作，由客户在页面上勾选条款后提交。
    """
    quote_id = arguments.get("quote_id")
    try:
        quote_id = int(quote_id)
    except (TypeError, ValueError):
        return ToolResult(
            name="prepare_order",
            ok=False,
            error_code="QUOTE_ARGUMENTS_INVALID",
            message="需要先有有效报价才能下单。",
        )

    quote = await CommerceService(session).get_quote(quote_id)
    if quote is None:
        return ToolResult(
            name="prepare_order",
            ok=False,
            error_code="QUOTE_NOT_FOUND",
            message="报价不存在。",
        )
    record, _items = quote
    if record.status not in ORDERABLE_QUOTE_STATUSES:
        return ToolResult(
            name="prepare_order",
            ok=False,
            error_code="QUOTE_NOT_CONFIRMED",
            message="请先确认报价，再创建订单。",
        )
    return ToolResult(
        name="prepare_order",
        ok=True,
        requires_confirmation=True,
        data={"quote_id": record.id, "total": str(record.total), "currency": record.currency},
        message="订单需要你本人核对条款后确认创建。",
    )


def build_default_registry() -> ToolRegistry:
    """构建默认工具白名单。未注册的工具无法被模型调用。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="search_products",
            description="按使用场景、关键词检索候选机器人产品。只读。",
            risk=TOOL_RISK_READ,
            handler=_search_products,
        )
    )
    registry.register(
        ToolSpec(
            name="create_quote",
            description="为已选定产品生成正式报价，金额由价格服务复算。",
            risk=TOOL_RISK_WRITE_LOCAL,
            handler=_create_quote,
        )
    )
    registry.register(
        ToolSpec(
            name="prepare_order",
            description="校验报价并准备下单动作；不直接创建订单，需用户确认。",
            risk=TOOL_RISK_WRITE_COMMIT,
            handler=_prepare_order,
            requires_confirmation=True,
        )
    )
    return registry
