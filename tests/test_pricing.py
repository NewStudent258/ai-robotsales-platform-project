"""价格规则库与税率/折扣的单元测试。

ARCH.md §2.4 要求金额由确定性服务计算，因此定价必须**先复现失败、
再验证通过**，且顺序契约（先折扣、后计税）不得被静默改动。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.pricing import PricingRule
from app.services.pricing_service import PriceLine, PricingService


def line(unit_price: str, quantity: int, product_id: int = 1) -> PriceLine:
    price = Decimal(unit_price)
    return PriceLine(
        product_id=product_id,
        product_name=f"P{product_id}",
        unit_price=price,
        quantity=quantity,
        line_total=price * quantity,
    )


def rule(
    code: str,
    kind: str,
    action: dict,
    *,
    priority: int = 0,
    condition: dict | None = None,
    version: int = 1,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> PricingRule:
    return PricingRule(
        code=code,
        name=code,
        kind=kind,
        priority=priority,
        version=version,
        status="ACTIVE",
        condition=condition or {},
        action=action,
        effective_from=effective_from,
        effective_to=effective_to,
    )


class TestNoRulesBehaviour:
    """无规则时必须与升级前一致，保证历史报价可复算。"""

    def test_no_rules_means_zero_tax_and_zero_discount(self, session_factory) -> None:
        service = PricingService.__new__(PricingService)
        result = service.evaluate([], [line("1000.00", 2)])
        assert result.subtotal == Decimal("2000.00")
        assert result.discount == Decimal("0.00")
        assert result.tax == Decimal("0.00")
        assert result.total == Decimal("2000.00")

    def test_missing_industry_context_does_not_break(self) -> None:
        service = PricingService.__new__(PricingService)
        result = service.evaluate([], [line("100.00", 1)], context={})
        assert result.total == Decimal("100.00")


class TestTaxCalculation:
    def test_tax_applied_on_subtotal_when_no_discount(self) -> None:
        service = PricingService.__new__(PricingService)
        tax = rule("TAX13", "tax", {"type": "rate", "value": "0.13"})
        result = service.evaluate([tax], [line("1000.00", 1)])
        assert result.tax == Decimal("130.00")
        assert result.total == Decimal("1130.00")

    def test_tax_is_charged_on_discounted_base_not_original(self) -> None:
        """顺序契约：必须先算折扣再计税，否则会多收税。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule("TAX13", "tax", {"type": "rate", "value": "0.13"}),
            rule(
                "D10",
                "discount",
                {"type": "percent", "value": "0.10"},
                priority=100,
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            ),
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.subtotal == Decimal("1000.00")
        assert result.discount == Decimal("100.00")
        assert result.taxable_base == Decimal("900.00")
        # 900 * 0.13 = 117，而不是 1000 * 0.13 = 130
        assert result.tax == Decimal("117.00")
        assert result.total == Decimal("1017.00")

    def test_empty_condition_tax_applies_globally(self) -> None:
        service = PricingService.__new__(PricingService)
        tax = rule("TAX", "tax", {"type": "rate", "value": "0.06"})
        result = service.evaluate([tax], [line("100.00", 3)])
        assert result.tax == Decimal("18.00")


class TestDiscountSelection:
    """「择一」：最高优先级生效，结果必须可复算。"""

    def test_highest_priority_discount_wins(self) -> None:
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "LOW",
                "discount",
                {"type": "percent", "value": "0.05"},
                priority=10,
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            ),
            rule(
                "HIGH",
                "discount",
                {"type": "percent", "value": "0.20"},
                priority=100,
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            ),
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.discount == Decimal("200.00")
        assert [applied.code for applied in result.applied_rules] == ["HIGH"]

    def test_discounts_are_not_stacked(self) -> None:
        """两条规则同时命中时只取一条，不得叠加为 25%。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "A",
                "discount",
                {"type": "percent", "value": "0.10"},
                priority=100,
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            ),
            rule(
                "B",
                "discount",
                {"type": "percent", "value": "0.15"},
                priority=50,
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            ),
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.discount == Decimal("100.00")

    def test_condition_not_met_gives_no_discount(self) -> None:
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "BULK",
                "discount",
                {"type": "percent", "value": "0.50"},
                condition={"all": [{"field": "quantity", "op": "gte", "value": 100}]},
            )
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.discount == Decimal("0.00")
        assert result.applied_rules == []

    def test_fixed_amount_discount(self) -> None:
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "COUPON",
                "discount",
                {"type": "fixed", "value": "300.00"},
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            )
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.discount == Decimal("300.00")
        assert result.total == Decimal("700.00")

    def test_discount_cannot_exceed_subtotal(self) -> None:
        """超大固定折扣不得产生负数报价。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "HUGE",
                "discount",
                {"type": "fixed", "value": "9999.00"},
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            )
        ]
        result = service.evaluate(rules, [line("100.00", 1)])
        assert result.discount == Decimal("100.00")
        assert result.taxable_base == Decimal("0.00")
        assert result.total == Decimal("0.00")

    def test_unknown_condition_field_does_not_match(self) -> None:
        """字段名写错时默认不给折扣，避免配置笔误放大优惠。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "TYPO",
                "discount",
                {"type": "percent", "value": "0.50"},
                condition={"all": [{"field": "quantitty", "op": "gte", "value": 1}]},
            )
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.discount == Decimal("0.00")

    def test_type_mismatch_condition_does_not_crash(self) -> None:
        """脏配置不得中断报价。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "BAD",
                "discount",
                {"type": "percent", "value": "0.10"},
                condition={"all": [{"field": "quantity", "op": "gte", "value": "abc"}]},
            )
        ]
        result = service.evaluate(rules, [line("1000.00", 1)])
        assert result.discount == Decimal("0.00")

    def test_use_case_and_industry_conditions(self) -> None:
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "EDU",
                "discount",
                {"type": "percent", "value": "0.20"},
                condition={"all": [{"field": "industry", "op": "eq", "value": "教育"}]},
            )
        ]
        hit = service.evaluate(rules, [line("1000.00", 1)], context={"industry": "教育"})
        miss = service.evaluate(rules, [line("1000.00", 1)], context={"industry": "工业"})
        assert hit.discount == Decimal("200.00")
        assert miss.discount == Decimal("0.00")

    def test_in_operator(self) -> None:
        service = PricingService.__new__(PricingService)
        rules = [
            rule(
                "MULTI",
                "discount",
                {"type": "percent", "value": "0.10"},
                condition={"all": [{"field": "industry", "op": "in", "value": ["教育", "科研"]}]},
            )
        ]
        assert service.evaluate(
            rules, [line("100.00", 1)], context={"industry": "科研"}
        ).discount == Decimal("10.00")


class TestRoundingAndRecomputability:
    def test_amounts_quantized_to_cents(self) -> None:
        """0.333 比例的税额必须量化到分，不能出现浮点尾巴。"""
        service = PricingService.__new__(PricingService)
        tax = rule("TAX", "tax", {"type": "rate", "value": "0.13"})
        result = service.evaluate([tax], [line("333.33", 1)])
        assert result.tax == Decimal("43.33")
        assert result.total == Decimal("376.66")

    def test_same_input_yields_same_result(self) -> None:
        """可复算性：同一输入多次计算必须完全一致。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule("TAX", "tax", {"type": "rate", "value": "0.06"}),
            rule(
                "D",
                "discount",
                {"type": "percent", "value": "0.15"},
                priority=1,
                condition={"all": [{"field": "quantity", "op": "gte", "value": 1}]},
            ),
        ]
        first = service.evaluate(rules, [line("1234.56", 3)])
        second = service.evaluate(rules, [line("1234.56", 3)])
        assert first.total == second.total

    def test_snapshot_records_rule_versions(self) -> None:
        """快照必须固化规则版本，否则历史报价无法追溯依据。"""
        service = PricingService.__new__(PricingService)
        rules = [
            rule("TAX", "tax", {"type": "rate", "value": "0.06"}, version=3),
        ]
        snapshot = service.evaluate(rules, [line("100.00", 1)]).snapshot()
        assert snapshot["rules"][0]["version"] == 3
        assert snapshot["pricing_policy"] == "rule-based-v2"


@pytest.mark.asyncio
async def test_rule_ordering_is_deterministic(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            rule(
                "B",
                "discount",
                {"type": "percent", "value": "0.10"},
                priority=5,
            )
        )
        session.add(
            rule(
                "A",
                "discount",
                {"type": "percent", "value": "0.20"},
                priority=5,
            )
        )
        await session.commit()
        loaded = await PricingService(session).load_active_rules()
    assert [item.code for item in loaded] == ["A", "B"]


@pytest.mark.asyncio
async def test_inactive_and_out_of_window_rules_excluded(session_factory) -> None:
    """状态非 ACTIVE 或不在生效窗口内的规则必须被排除。"""
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        draft = rule("DRAFT", "discount", {"type": "percent", "value": "0.50"})
        draft.status = "DRAFT"
        expired = rule(
            "OLD",
            "discount",
            {"type": "percent", "value": "0.50"},
            effective_to=now - timedelta(days=1),
        )
        future = rule(
            "NEW",
            "discount",
            {"type": "percent", "value": "0.50"},
            effective_from=now + timedelta(days=1),
        )
        live = rule(
            "LIVE",
            "discount",
            {"type": "percent", "value": "0.10"},
            effective_from=now - timedelta(days=1),
            effective_to=now + timedelta(days=1),
        )
        session.add_all([draft, expired, future, live])
        await session.commit()
        loaded = await PricingService(session).load_active_rules()
    assert [item.code for item in loaded] == ["LIVE"]
