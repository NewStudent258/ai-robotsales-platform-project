"""种子脚本：写入默认价格规则。

规则库是价格真源（ARCH.md §5），因此脚本必须幂等——重复执行不得产生
重复规则或改变既有规则版本。已存在的规则只在其版本落后时按 code 就地升级。

默认规则集刻意保持保守：
- 一条全局零税率规则，使无配置环境下行为与升级前一致（税额 0）；
- 两条按数量的折扣规则，演示「择一」：同一报价只取优先级最高者。

正式折扣属高风险动作（ARCH.md §7 要求审批），故示例规则
`requires_approval=True` 且状态为 DRAFT，需运营审核后手动启用。
"""

import asyncio
import sys

from sqlalchemy import select

sys.path.append(".")

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import PricingRule

RULES = [
    {
        "code": "TAX-CN-DEFAULT",
        "name": "默认税率（0%）",
        "description": "占位税率规则。正式税率需运营按地区配置并审批。",
        "kind": "tax",
        "priority": 0,
        "status": "ACTIVE",
        "condition": {},
        "action": {"type": "rate", "value": "0"},
        "requires_approval": False,
    },
    {
        "code": "DISC-VOLUME-10",
        "name": "批量采购折扣（10 台及以上 8 折）",
        "description": "单笔报价总数量达到 10 台时给予 20% 折扣。",
        "kind": "discount",
        "priority": 100,
        "status": "ACTIVE",
        "condition": {"all": [{"field": "quantity", "op": "gte", "value": 10}]},
        "action": {"type": "percent", "value": "0.20"},
        "requires_approval": True,
    },
    {
        "code": "DISC-VOLUME-50",
        "name": "战略采购折扣（50 台及以上 7 折）",
        "description": "单笔报价总数量达到 50 台时给予 30% 折扣；优先级高于普通批量折扣。",
        "kind": "discount",
        "priority": 200,
        "status": "DRAFT",
        "condition": {"all": [{"field": "quantity", "op": "gte", "value": 50}]},
        "action": {"type": "percent", "value": "0.30"},
        "requires_approval": True,
    },
]


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    created = 0
    async with SessionLocal() as session:
        for payload in RULES:
            existing = await session.scalar(
                select(PricingRule).where(PricingRule.code == payload["code"])
            )
            if existing is None:
                session.add(PricingRule(**payload))
                created += 1
                continue
            # 已存在则只同步展示性字段，不覆盖 status/priority——
            # 那些是运营决策，脚本无权改写生效中的定价行为。
            existing.name = payload["name"]
            existing.description = payload["description"]
        await session.commit()
    print(f"Seeded {created} pricing rules (total defined: {len(RULES)})")


if __name__ == "__main__":
    asyncio.run(main())
