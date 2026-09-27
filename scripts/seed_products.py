import asyncio
import sys
from decimal import Decimal

from sqlalchemy import select

sys.path.append(".")

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import Product

PRODUCTS = [
    {
        "sku": "RB-EDU-001",
        "slug": "edu-vision-robot",
        "name": "Edu Vision 教育机器人",
        "description": "面向课堂和机器人实验室的视觉开发套件，支持目标识别与基础导航。",
        "base_price": Decimal("3999.00"),
        "capabilities": ["目标识别", "基础导航", "Python SDK"],
        "use_cases": ["教育", "科研", "开发者"],
        "specs": {"compute": "8GB edge AI", "camera": "8MP", "battery_hours": 2},
    },
    {
        "sku": "RB-INS-002",
        "slug": "inspection-robot-pro",
        "name": "Inspect Pro 巡检机器人",
        "description": "面向园区和仓储巡检的移动机器人，支持环境感知、路线规划和远程运维。",
        "base_price": Decimal("18999.00"),
        "capabilities": ["环境感知", "路线规划", "远程运维"],
        "use_cases": ["巡检", "仓储", "园区"],
        "specs": {"compute": "16GB edge AI", "camera": "4K", "battery_hours": 8},
    },
]


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        for payload in PRODUCTS:
            existing = await session.scalar(select(Product).where(Product.sku == payload["sku"]))
            if existing is None:
                session.add(Product(**payload))
        await session.commit()
    print(f"Seeded {len(PRODUCTS)} products")


if __name__ == "__main__":
    asyncio.run(main())
