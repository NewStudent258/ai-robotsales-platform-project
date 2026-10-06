"""价格规则库的契约与端到端定价测试。

覆盖 P1 的业务承诺：规则生效窗口、折扣择一、税额基于折后价、
以及「报价固化规则版本」的可追溯要求。
"""

from datetime import datetime, timedelta, timezone

from app.models.commerce import Quote
from app.models.pricing import PricingRule

PRODUCT = {
    "sku": "RBT-200",
    "slug": "rbt-200",
    "name": "搬运机器人 AMR",
    "description": "仓储搬运场景",
    "base_price": "1000.00",
    "currency": "CNY",
    "use_cases": ["仓储"],
}


async def _seed_product(client) -> int:
    response = await client.post(
        "/api/v1/products", json=PRODUCT, headers={"X-Admin-Token": "test-admin-token"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _add_rule(session_factory, **overrides) -> None:
    defaults = {
        "code": "R1",
        "name": "规则",
        "kind": "tax",
        "priority": 0,
        "version": 1,
        "status": "ACTIVE",
        "condition": {},
        "action": {"type": "rate", "value": "0"},
    }
    defaults.update(overrides)
    async with session_factory() as session:
        session.add(PricingRule(**defaults))
        await session.commit()


async def _quote(client, product_id: int, quantity: int) -> dict:
    response = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "李四",
            "customer_email": "li@example.com",
            "items": [{"product_id": product_id, "quantity": quantity}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestRuleDrivenPricingEndToEnd:
    async def test_tax_rule_applied_via_api(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory, code="TAX13", kind="tax", action={"type": "rate", "value": "0.13"}
        )
        quote = await _quote(client, product_id, 1)
        assert quote["subtotal"] == "1000.00"
        assert quote["tax"] == "130.00"
        assert quote["total"] == "1130.00"

    async def test_volume_discount_applied_via_api(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="VOL",
            kind="discount",
            priority=100,
            condition={"all": [{"field": "quantity", "op": "gte", "value": 10}]},
            action={"type": "percent", "value": "0.20"},
        )
        below = await _quote(client, product_id, 9)
        at = await _quote(client, product_id, 10)
        assert below["discount"] == "0.00"
        assert at["discount"] == "2000.00"
        assert at["total"] == "8000.00"

    async def test_discount_and_tax_combined_order(self, client, session_factory) -> None:
        """顺序契约：先折扣后计税，税额基于折后金额。"""
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="TAX13",
            kind="tax",
            action={"type": "rate", "value": "0.13"},
        )
        await _add_rule(
            session_factory,
            code="VOL",
            kind="discount",
            priority=100,
            condition={"all": [{"field": "quantity", "op": "gte", "value": 10}]},
            action={"type": "percent", "value": "0.20"},
        )
        quote = await _quote(client, product_id, 10)
        assert quote["subtotal"] == "10000.00"
        assert quote["discount"] == "2000.00"
        # 税基于 8000 而非 10000
        assert quote["tax"] == "1040.00"
        assert quote["total"] == "9040.00"

    async def test_only_one_discount_applies(self, client, session_factory) -> None:
        """择一：两条同时命中也只减一次。"""
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="A",
            kind="discount",
            priority=100,
            condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            action={"type": "percent", "value": "0.10"},
        )
        await _add_rule(
            session_factory,
            code="B",
            kind="discount",
            priority=50,
            condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            action={"type": "percent", "value": "0.30"},
        )
        quote = await _quote(client, product_id, 1)
        assert quote["discount"] == "100.00"
        codes = [rule["code"] for rule in quote["applied_rules"]]
        assert codes == ["A"]

    async def test_draft_rule_not_applied(self, client, session_factory) -> None:
        """未启用规则不得影响金额。"""
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="DRAFT",
            kind="discount",
            status="DRAFT",
            condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            action={"type": "percent", "value": "0.90"},
        )
        quote = await _quote(client, product_id, 1)
        assert quote["discount"] == "0.00"

    async def test_expired_rule_not_applied(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="OLD",
            kind="discount",
            effective_to=datetime.now(timezone.utc) - timedelta(days=1),
            condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            action={"type": "percent", "value": "0.90"},
        )
        quote = await _quote(client, product_id, 1)
        assert quote["discount"] == "0.00"

    async def test_future_rule_not_applied(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="FUTURE",
            kind="discount",
            effective_from=datetime.now(timezone.utc) + timedelta(days=1),
            condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            action={"type": "percent", "value": "0.90"},
        )
        quote = await _quote(client, product_id, 1)
        assert quote["discount"] == "0.00"


class TestSnapshotTraceability:
    async def test_snapshot_freezes_rule_version(self, client, session_factory) -> None:
        """报价必须固化规则版本，否则事后改动规则会让历史金额无法解释。"""
        product_id = await _seed_product(client)
        await _add_rule(
            session_factory,
            code="TAX6",
            kind="tax",
            version=7,
            action={"type": "rate", "value": "0.06"},
        )
        quote = await _quote(client, product_id, 1)
        async with session_factory() as session:
            row = await session.get(Quote, quote["id"])
        snapshot = row.snapshot
        assert snapshot["pricing_policy"] == "rule-based-v2"
        assert snapshot["rules"][0]["version"] == 7
        assert snapshot["rules"][0]["code"] == "TAX6"
        assert snapshot["total"] == "1060.00"

    async def test_snapshot_records_line_details(self, client, session_factory) -> None:
        product_id = await _seed_product(client)
        quote = await _quote(client, product_id, 3)
        async with session_factory() as session:
            row = await session.get(Quote, quote["id"])
        assert row.snapshot["items"][0]["quantity"] == 3
        assert row.snapshot["items"][0]["line_total"] == "3000.00"
        assert row.snapshot["product_ids"] == [product_id]
