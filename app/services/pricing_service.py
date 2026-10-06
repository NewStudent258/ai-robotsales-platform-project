"""确定性价格服务。

ARCH.md §1 要求价格服务是「唯一价格真源，纯计算优先」。
本模块只做纯计算与规则评估，不写库；报价落库由 `CommerceService` 负责。

定价顺序（顺序本身是契约，改动即破坏可复算性）：
1. 行小计 = 产品基础价 × 数量（服务端取价，客户端无权传价）；
2. 折扣：所有命中的折扣规则按 `(priority desc, code asc)` 排序后**取第一条**；
3. 折后小计 = 行小计合计 − 折扣额（不得为负）；
4. 税费 = 折后小计 × 税率（对折后金额计税，不是对原价计税）；
5. 合计 = 折后小计 + 税费。

取整策略：每一步都用 ROUND_HALF_UP 量化到分，且**先算折扣再算税**，
保证同一输入永远得到同一结果。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import (
    DISCOUNT_FIXED,
    DISCOUNT_PERCENT,
    OP_EQ,
    OP_GTE,
    OP_IN,
    OP_LTE,
    RULE_ACTIVE,
    RULE_KIND_DISCOUNT,
    RULE_KIND_TAX,
    PricingRule,
    is_effective,
)

CENT = Decimal("0.01")
# 价格策略版本标识，随定价规则变更升级，用于报价快照与复算。
PRICING_POLICY = "rule-based-v2"


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class PriceLine:
    """单行定价结果。"""

    product_id: int
    product_name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


@dataclass(slots=True)
class AppliedRule:
    """本报价实际生效的规则，固化进快照以便复算与审计。"""

    code: str
    name: str
    kind: str
    version: int
    priority: int
    amount: Decimal
    detail: str


@dataclass(slots=True)
class PriceResult:
    """完整定价结果。金额全部为 Decimal，避免浮点误差。"""

    currency: str
    lines: list[PriceLine] = field(default_factory=list)
    subtotal: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    taxable_base: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    applied_rules: list[AppliedRule] = field(default_factory=list)
    policy: str = PRICING_POLICY

    def snapshot(self) -> dict:
        """固化进 Quote.snapshot 的可复算载荷。"""
        return {
            "pricing_policy": self.policy,
            "currency": self.currency,
            "subtotal": str(self.subtotal),
            "discount": str(self.discount),
            "taxable_base": str(self.taxable_base),
            "tax": str(self.tax),
            "total": str(self.total),
            "rules": [
                {
                    "code": rule.code,
                    "name": rule.name,
                    "kind": rule.kind,
                    "version": rule.version,
                    "priority": rule.priority,
                    "amount": str(rule.amount),
                }
                for rule in self.applied_rules
            ],
        }


class PricingService:
    """纯计算优先的价格服务。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def load_active_rules(self, now: datetime | None = None) -> list[PricingRule]:
        """读取全部生效中的规则，按「优先级降序、code 升序」稳定排序。

        稳定的排序是「择一」结果可复算的前提：同一批规则永远选出同一条。
        """
        moment = now or datetime.now(timezone.utc)
        rules = list(
            await self.session.scalars(select(PricingRule).where(PricingRule.status == RULE_ACTIVE))
        )
        effective = [rule for rule in rules if is_effective(rule, moment)]
        return sorted(effective, key=lambda rule: (-rule.priority, rule.code))

    def evaluate(
        self,
        rules: list[PricingRule],
        lines: list[PriceLine],
        *,
        context: dict | None = None,
    ) -> PriceResult:
        """对给定行执行定价。纯函数，便于单元测试与复算。

        本方法**自行排序**，不依赖调用方传入有序列表：否则「择一」结果会
        随入参顺序漂移，金额不可复算。
        """
        payload = context or {}
        subtotal = _money(sum((line.line_total for line in lines), Decimal("0")))
        currency = str(payload.get("currency") or "CNY")
        ordered = sorted(rules, key=lambda rule: (-rule.priority, rule.code))

        matched_discounts = [
            rule
            for rule in ordered
            if rule.kind == RULE_KIND_DISCOUNT and self._matches(rule, lines, payload)
        ]
        # 择一：已按优先级排序，取第一条即最高优先级。
        applied: list[AppliedRule] = []
        discount = Decimal("0.00")
        if matched_discounts:
            chosen = matched_discounts[0]
            discount = self._discount_amount(chosen, subtotal)
            applied.append(
                AppliedRule(
                    code=chosen.code,
                    name=chosen.name,
                    kind=chosen.kind,
                    version=chosen.version,
                    priority=chosen.priority,
                    amount=discount,
                    detail=self._describe(chosen, discount),
                )
            )

        # 折扣不得超过小计本身，否则会出现负数报价。
        discount = min(discount, subtotal)
        taxable_base = _money(subtotal - discount)

        tax = Decimal("0.00")
        matched_taxes = [
            rule
            for rule in ordered
            if rule.kind == RULE_KIND_TAX and self._matches(rule, lines, payload)
        ]
        if matched_taxes:
            tax_rule = matched_taxes[0]
            tax = self._tax_amount(tax_rule, taxable_base)
            applied.append(
                AppliedRule(
                    code=tax_rule.code,
                    name=tax_rule.name,
                    kind=tax_rule.kind,
                    version=tax_rule.version,
                    priority=tax_rule.priority,
                    amount=tax,
                    detail=self._describe(tax_rule, tax),
                )
            )

        return PriceResult(
            currency=currency,
            lines=lines,
            subtotal=subtotal,
            discount=discount,
            taxable_base=taxable_base,
            tax=tax,
            total=_money(taxable_base + tax),
            applied_rules=applied,
        )

    @staticmethod
    def _matches(rule: PricingRule, lines: list[PriceLine], context: dict) -> bool:
        """评估规则条件。

        条件以 `{"all": [...]}` 表达，全部子条件必须成立。
        空条件视为恒真（用于全局默认税率）。
        """
        condition = rule.condition or {}
        clauses = condition.get("all", [])
        if not clauses:
            return True

        total_quantity = sum(line.quantity for line in lines)
        facts = {
            "quantity": total_quantity,
            "line_count": len(lines),
            "subtotal": sum((line.line_total for line in lines), Decimal("0")),
            "currency": context.get("currency"),
            "industry": context.get("industry"),
            "use_case": context.get("use_case"),
        }
        return all(PricingService._clause_holds(clause, facts) for clause in clauses)

    @staticmethod
    def _clause_holds(clause: dict, facts: dict) -> bool:
        field_name = clause.get("field")
        operator = clause.get("op")
        expected = clause.get("value")
        if field_name not in facts:
            # 未知字段一律不命中：默认不给予优惠，避免配置笔误放大折扣。
            return False
        actual = facts[field_name]
        if actual is None:
            return False

        try:
            if operator == OP_EQ:
                return str(actual) == str(expected)
            if operator == OP_IN:
                return str(actual) in {str(item) for item in (expected or [])}
            if operator == OP_GTE:
                return Decimal(str(actual)) >= Decimal(str(expected))
            if operator == OP_LTE:
                return Decimal(str(actual)) <= Decimal(str(expected))
        except (ArithmeticError, TypeError, ValueError):
            # 类型不匹配按不命中处理，不让脏配置中断报价。
            return False
        return False

    @staticmethod
    def _discount_amount(rule: PricingRule, subtotal: Decimal) -> Decimal:
        action = rule.action or {}
        action_type = action.get("type")
        raw = Decimal(str(action.get("value", "0")))
        if action_type == DISCOUNT_PERCENT:
            return _money(subtotal * raw)
        if action_type == DISCOUNT_FIXED:
            return _money(raw)
        return Decimal("0.00")

    @staticmethod
    def _tax_amount(rule: PricingRule, base: Decimal) -> Decimal:
        action = rule.action or {}
        rate = Decimal(str(action.get("value", "0")))
        return _money(base * rate)

    @staticmethod
    def _describe(rule: PricingRule, amount: Decimal) -> str:
        action = rule.action or {}
        raw = str(action.get("value", "0"))
        if rule.kind == RULE_KIND_DISCOUNT and action.get("type") == DISCOUNT_PERCENT:
            percent = (Decimal(raw) * 100).normalize()
            return f"{rule.name}（{percent}% 折扣，减免 {amount}）"
        if rule.kind == RULE_KIND_TAX:
            percent = (Decimal(raw) * 100).normalize()
            return f"{rule.name}（税率 {percent}%）"
        return f"{rule.name}（减免 {amount}）"
