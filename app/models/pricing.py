"""价格规则库。

ARCH.md §5 要求「规则库」具备优先级、条件、动作、版本与生效时间。
本模块是**确定性**价格真源：模型与 Agent 均无权计算或改写金额。

规则模型设计：
- `condition` 用受控的 JSON 条件表达（字段/操作符/值），不做任意表达式求值，
  避免把代码注入面引入配置；
- `action` 描述折扣或税费的施加方式；
- 同一报价行上多条规则命中时**择一**（优先级最高者生效），
  使金额可预测、可解释、可复算。
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 规则类型。
RULE_KIND_DISCOUNT = "discount"
RULE_KIND_TAX = "tax"
RULE_KINDS = {RULE_KIND_DISCOUNT, RULE_KIND_TAX}

# 折扣动作。
DISCOUNT_PERCENT = "percent"  # 按比例减免，如 0.05 表示 5%
DISCOUNT_FIXED = "fixed"  # 按固定金额减免（本币最小单位）

# 条件操作符。刻意保持极小集合，避免成为表达式引擎。
OP_EQ = "eq"
OP_IN = "in"
OP_GTE = "gte"
OP_LTE = "lte"
OPERATORS = {OP_EQ, OP_IN, OP_GTE, OP_LTE}

# 规则状态。
RULE_DRAFT = "DRAFT"
RULE_ACTIVE = "ACTIVE"
RULE_RETIRED = "RETIRED"


class PricingRule(TimestampMixin, Base):
    """价格规则。版本化并带生效窗口，报价必须固化所用规则版本。"""

    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    # 数值越大优先级越高；同优先级由 code 稳定排序决定，保证结果可复算。
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=RULE_DRAFT, index=True)

    # 条件：{"all": [{"field": "quantity", "op": "gte", "value": 10}, ...]}
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    # 动作：{"type": "percent", "value": "0.05"} 或 {"type": "rate", "value": "0.06"}
    action: Mapped[dict] = mapped_column(JSON, default=dict)

    # 生效窗口。为空表示不限制该侧边界。
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 是否需要人工审批后才可用于正式报价（高风险折扣）。
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)


def is_effective(rule: PricingRule, now: datetime | None = None) -> bool:
    """判断规则在给定时刻是否生效。

    统一按 UTC 比较；SQLite 读回的 naive datetime 视为 UTC，
    与 `commerce_service._utc` 的处理保持一致。
    """
    moment = now or datetime.now(timezone.utc)
    start = rule.effective_from
    end = rule.effective_to
    if start is not None:
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        if moment < start:
            return False
    if end is not None:
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
        if moment > end:
            return False
    return True
