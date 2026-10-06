"""报价版本递增与重新报价的集成测试（P1）。

已确认的语义：
- 重新报价**新建一条 Quote**并通过 `root_quote_id` 关联，旧版标记 `SUPERSEDED`；
- 不就地改写原报价，保证已成交/已确认报价的审计链完整；
- `expected_version` 提供乐观并发保护。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.commerce import Quote
from app.models.pricing import PricingRule

PRODUCT = {
    "sku": "RBT-100",
    "slug": "rbt-100",
    "name": "巡检机器人 X1",
    "description": "适用于仓储巡检场景的自主移动机器人",
    "base_price": "1000.00",
    "currency": "CNY",
    "use_cases": ["仓储巡检"],
}


async def _seed_product(client) -> int:
    response = await client.post(
        "/api/v1/products", json=PRODUCT, headers={"X-Admin-Token": "test-admin-token"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_quote(client, product_id: int, quantity: int = 1) -> dict:
    response = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "张三",
            "customer_email": "zhang@example.com",
            "items": [{"product_id": product_id, "quantity": quantity}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _revise(client, quote: dict, product_id: int, quantity: int, expected_version: int):
    return await client.post(
        f"/api/v1/quotes/{quote['id']}/revise",
        headers={"X-Quote-Token": quote["access_token"]},
        json={
            "customer_name": quote["customer_name"],
            "customer_email": quote["customer_email"],
            "items": [{"product_id": product_id, "quantity": quantity}],
            "expected_version": expected_version,
        },
    )


class TestQuoteVersionIncrement:
    async def test_revision_creates_new_version_row(self, client, session_factory) -> None:
        """版本递增必须新建行，而不是就地改写。"""
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        response = await _revise(client, first, product_id, 3, expected_version=1)
        assert response.status_code == 201, response.text
        revision = response.json()

        assert revision["id"] != first["id"]
        assert revision["version"] == 2

        async with session_factory() as session:
            rows = list(await session.scalars(select(Quote).order_by(Quote.version)))
        assert len(rows) == 2
        # 原报价金额保留，未被覆盖。
        assert rows[0].total == Decimal("1000.00")
        assert rows[0].status == "SUPERSEDED"

    async def test_original_marked_superseded_and_linked(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        revision = (await _revise(client, first, product_id, 2, 1)).json()

        async with session_factory() as session:
            original = await session.get(Quote, first["id"])
        assert original.status == "SUPERSEDED"
        assert original.superseded_by_id == revision["id"]
        assert original.root_quote_id == original.id

    async def test_revision_shares_root(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        revision = (await _revise(client, first, product_id, 2, 1)).json()
        async with session_factory() as session:
            row = await session.get(Quote, revision["id"])
        assert row.root_quote_id == first["id"]

    async def test_chain_accumulates_versions(self, client) -> None:
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        second = (await _revise(client, first, product_id, 2, 1)).json()
        third = (await _revise(client, second, product_id, 5, 2)).json()

        versions = [(item["version"], item["status"]) for item in third["versions"]]
        assert versions == [
            (1, "SUPERSEDED"),
            (2, "SUPERSEDED"),
            (3, "PENDING_CONFIRMATION"),
        ]
        assert third["versions"][-1]["is_current"] is True

    async def test_revision_recomputes_amount(self, client) -> None:
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        revision = (await _revise(client, first, product_id, 3, 1)).json()
        assert revision["subtotal"] == "3000.00"
        assert revision["total"] == "3000.00"


class TestRevisionGuards:
    async def test_stale_expected_version_conflicts(self, client) -> None:
        """乐观锁：过期版本必须拒绝，避免覆盖他人修改。"""
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        response = await _revise(client, first, product_id, 2, expected_version=99)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "QUOTE_VERSION_CONFLICT"

    async def test_superseded_quote_cannot_be_revised_again(self, client) -> None:
        """被取代的报价不得再次派生新版本，否则版本链会分叉。"""
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        await _revise(client, first, product_id, 2, 1)
        response = await _revise(client, first, product_id, 4, 1)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "QUOTE_NOT_REVISABLE"

    async def test_order_created_quote_cannot_be_revised(self, client, session_factory) -> None:
        """已建单报价必须冻结，否则已成交金额会被改写。"""
        product_id = await _seed_product(client)
        quote = await _create_quote(client, product_id, quantity=1)
        token = quote["access_token"]
        await client.post(
            f"/api/v1/quotes/{quote['id']}/confirm?version=1",
            headers={"X-Quote-Token": token},
        )
        await client.post(
            f"/api/v1/orders?quote_id={quote['id']}",
            headers={"X-Quote-Token": token, "Idempotency-Key": "revise-guard-1234"},
        )
        response = await _revise(client, quote, product_id, 5, expected_version=1)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "QUOTE_NOT_REVISABLE"

    async def test_requires_matching_access_token(self, client) -> None:
        product_id = await _seed_product(client)
        quote = await _create_quote(client, product_id, quantity=1)
        response = await client.post(
            f"/api/v1/quotes/{quote['id']}/revise",
            headers={"X-Quote-Token": "wrong-token"},
            json={
                "customer_name": "张三",
                "customer_email": "zhang@example.com",
                "items": [{"product_id": product_id, "quantity": 2}],
                "expected_version": 1,
            },
        )
        # 令牌错误统一 404，不泄露报价是否存在。
        assert response.status_code == 404

    async def test_requires_expected_version(self, client) -> None:
        product_id = await _seed_product(client)
        quote = await _create_quote(client, product_id, quantity=1)
        response = await client.post(
            f"/api/v1/quotes/{quote['id']}/revise",
            headers={"X-Quote-Token": quote["access_token"]},
            json={
                "customer_name": "张三",
                "customer_email": "zhang@example.com",
                "items": [{"product_id": product_id, "quantity": 2}],
            },
        )
        assert response.status_code == 422

    async def test_client_cannot_inject_amount(self, client) -> None:
        """金额防篡改：extra=forbid 必须拒绝客户端传入的金额字段。"""
        product_id = await _seed_product(client)
        quote = await _create_quote(client, product_id, quantity=1)
        response = await client.post(
            f"/api/v1/quotes/{quote['id']}/revise",
            headers={"X-Quote-Token": quote["access_token"]},
            json={
                "customer_name": "张三",
                "customer_email": "zhang@example.com",
                "items": [{"product_id": product_id, "quantity": 2}],
                "expected_version": 1,
                "total": "0.01",
            },
        )
        assert response.status_code == 422

    async def test_unknown_quote_returns_404(self, client) -> None:
        product_id = await _seed_product(client)
        quote = await _create_quote(client, product_id, quantity=1)
        response = await client.post(
            "/api/v1/quotes/999999/revise",
            headers={"X-Quote-Token": quote["access_token"]},
            json={
                "customer_name": "张三",
                "customer_email": "zhang@example.com",
                "items": [{"product_id": product_id, "quantity": 1}],
                "expected_version": 1,
            },
        )
        assert response.status_code == 404

    async def test_revision_expiry_reset(self, client) -> None:
        """新版本必须重新获得完整有效期，而不是继承旧版的剩余时间。"""
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        revision = (await _revise(client, first, product_id, 2, 1)).json()
        expiry = datetime.fromisoformat(revision["expires_at"])
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        assert expiry > datetime.now(timezone.utc) + timedelta(days=6)


class TestRevisionWithPricingRules:
    async def test_revision_applies_current_rules(self, client, session_factory) -> None:
        """重新报价必须按当前生效规则重算，而不是复制旧金额。"""
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)
        assert first["total"] == "1000.00"

        async with session_factory() as session:
            session.add(
                PricingRule(
                    code="TAX6",
                    name="税率 6%",
                    kind="tax",
                    priority=0,
                    version=1,
                    status="ACTIVE",
                    condition={},
                    action={"type": "rate", "value": "0.06"},
                )
            )
            await session.commit()

        revision = (await _revise(client, first, product_id, 1, 1)).json()
        assert revision["tax"] == "60.00"
        assert revision["total"] == "1060.00"

    async def test_quote_created_before_rules_keeps_price(self, client, session_factory) -> None:
        """规则变更不得改写既有报价：历史金额必须冻结。"""
        product_id = await _seed_product(client)
        first = await _create_quote(client, product_id, quantity=1)

        async with session_factory() as session:
            session.add(
                PricingRule(
                    code="TAX6",
                    name="税率 6%",
                    kind="tax",
                    priority=0,
                    version=1,
                    status="ACTIVE",
                    condition={},
                    action={"type": "rate", "value": "0.06"},
                )
            )
            await session.commit()

        response = await client.get(
            f"/api/v1/quotes/{first['id']}",
            headers={"X-Quote-Token": first["access_token"]},
        )
        assert response.json()["total"] == "1000.00"


class TestAppliedRulesExposure:
    async def test_applied_rules_returned_for_explainability(self, client, session_factory) -> None:
        """报价必须说明金额依据，否则客户无法核对。"""
        product_id = await _seed_product(client)
        async with session_factory() as session:
            session.add(
                PricingRule(
                    code="TAX6",
                    name="增值税 6%",
                    kind="tax",
                    priority=0,
                    version=2,
                    status="ACTIVE",
                    condition={},
                    action={"type": "rate", "value": "0.06"},
                )
            )
            await session.commit()

        quote = await _create_quote(client, product_id, quantity=1)
        assert len(quote["applied_rules"]) == 1
        applied = quote["applied_rules"][0]
        assert applied["code"] == "TAX6"
        assert applied["version"] == 2
        assert applied["amount"] == "60.00"

    async def test_no_rules_means_empty_applied_rules(self, client) -> None:
        """无规则时不应编造优惠依据。"""
        product_id = await _seed_product(client)
        quote = await _create_quote(client, product_id, quantity=1)
        assert quote["applied_rules"] == []
        assert quote["discount"] == "0.00"
        assert quote["tax"] == "0.00"
