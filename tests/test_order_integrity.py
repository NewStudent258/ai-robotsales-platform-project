"""订单完整性回归测试。

覆盖三个已修复的 P0 缺陷：
1. 报价过期后仍可下单；
2. 并发同一幂等键导致 500，而非幂等回放；
3. 订单状态机与 ARCH.md §3 不一致（COMPLETED 不可达/非终态）。
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.commerce import Order, Quote
from app.services.commerce_service import ORDER_TRANSITIONS, CommerceService


async def _seed_product(client) -> dict:
    response = await client.post(
        "/api/v1/products",
        json={
            "sku": "INTEG-001",
            "slug": "integrity-robot",
            "name": "完整性测试机器人",
            "description": "用于订单完整性测试",
            "base_price": "100.00",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _confirmed_quote(client, product_id: int) -> tuple[dict, dict]:
    response = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "测试用户",
            "customer_email": "integrity@example.com",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    assert response.status_code == 201
    quote = response.json()
    headers = {"X-Quote-Token": quote["access_token"]}
    confirmed = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=headers
    )
    assert confirmed.status_code == 200
    return quote, headers


async def _expire_quote(session_factory, quote_id: int) -> None:
    """把报价有效期改到过去，模拟真实过期。"""
    async with session_factory() as session:
        quote = await session.get(Quote, quote_id)
        quote.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await session.commit()


async def test_expired_confirmed_quote_cannot_create_order(client, session_factory):
    """已确认但已过期的报价必须拒绝下单，且报价落为 EXPIRED。"""
    product = await _seed_product(client)
    quote, headers = await _confirmed_quote(client, product["id"])
    await _expire_quote(session_factory, quote["id"])

    response = await client.post(
        "/api/v1/orders",
        params={"quote_id": quote["id"]},
        headers={**headers, "Idempotency-Key": "expired-order-001"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "QUOTE_EXPIRED"

    latest = await client.get(f"/api/v1/quotes/{quote['id']}", headers=headers)
    assert latest.json()["status"] == "EXPIRED"

    async with session_factory() as session:
        orders = await session.get(Order, 1)
        assert orders is None, "过期报价不得产生任何订单"


async def test_concurrent_same_key_creates_single_order(client_factory):
    """同一幂等键并发下单必须全部成功回放同一个订单，不得返回 500。

    使用文件型 SQLite + 连接池，使并发请求各自持有独立连接，
    否则单连接内存库会让用例失去意义。
    """
    from sqlalchemy import func, select

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        product = await _seed_product(client)
        quote, headers = await _confirmed_quote(client, product["id"])
        request_headers = {**headers, "Idempotency-Key": "concurrent-key-001"}

        responses = await asyncio.gather(
            *[
                client.post(
                    "/api/v1/orders", params={"quote_id": quote["id"]}, headers=request_headers
                )
                for _ in range(6)
            ],
            return_exceptions=True,
        )

    failures = [r for r in responses if isinstance(r, BaseException)]
    assert not failures, f"并发下单抛出异常而非幂等回放: {failures}"
    assert [r.status_code for r in responses] == [201] * 6
    assert len({r.json()["id"] for r in responses}) == 1, "并发请求不应产生多个订单"

    async with client_factory() as session:
        count = await session.scalar(
            select(func.count(Order.id)).where(Order.quote_id == quote["id"])
        )
        assert count == 1, f"该报价应只有一个订单，实际 {count} 个"


async def test_sequential_replay_returns_same_order(client):
    """顺序重放同一幂等键返回同一订单。"""
    product = await _seed_product(client)
    quote, headers = await _confirmed_quote(client, product["id"])
    request_headers = {**headers, "Idempotency-Key": "replay-key-001"}

    first = await client.post(
        "/api/v1/orders", params={"quote_id": quote["id"]}, headers=request_headers
    )
    second = await client.post(
        "/api/v1/orders", params={"quote_id": quote["id"]}, headers=request_headers
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


async def test_order_state_machine_matches_arch(client, session_factory):
    """COMPLETED 必须可达且为终态；终态不得再迁移。"""
    product = await _seed_product(client)
    quote, headers = await _confirmed_quote(client, product["id"])
    created = await client.post(
        "/api/v1/orders",
        params={"quote_id": quote["id"]},
        headers={**headers, "Idempotency-Key": "state-key-001"},
    )
    order_id = created.json()["id"]

    async with session_factory() as session:
        service = CommerceService(session)
        processing = await service.transition_order(order_id, "PROCESSING")
        assert processing.status == "PROCESSING"
        completed = await service.transition_order(order_id, "COMPLETED")
        assert completed.status == "COMPLETED"

        with pytest.raises(Exception) as excinfo:
            await service.transition_order(order_id, "PROCESSING")
        assert getattr(excinfo.value, "detail", {}).get("code") == "ORDER_INVALID_TRANSITION"


def test_order_transitions_declare_terminal_states():
    """ARCH.md §3 的终态必须显式声明为空集。"""
    for terminal in ("COMPLETED", "CANCELLED", "EXPIRED"):
        assert ORDER_TRANSITIONS[terminal] == set(), f"{terminal} 必须是终态"
    assert "COMPLETED" in ORDER_TRANSITIONS["PROCESSING"]
    assert ORDER_TRANSITIONS["DRAFT"] == {"PENDING_CONFIRMATION", "EXPIRED"}


async def test_quote_total_matches_server_price(client):
    """金额回归：多商品报价由服务端计算。"""
    product = await _seed_product(client)
    second = await client.post(
        "/api/v1/products",
        json={
            "sku": "INTEG-002",
            "slug": "integrity-robot-two",
            "name": "完整性测试机器人二",
            "description": "用于金额回归",
            "base_price": "250.50",
        },
    )
    assert second.status_code == 201
    quote = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "测试用户",
            "customer_email": "integrity@example.com",
            "items": [
                {"product_id": product["id"], "quantity": 3},
                {"product_id": second.json()["id"], "quantity": 2},
            ],
        },
    )
    assert quote.status_code == 201
    assert Decimal(quote.json()["total"]) == Decimal("801.00")
