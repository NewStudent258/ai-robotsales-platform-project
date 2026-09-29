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
    {
        "sku": "RB-AMR-003",
        "slug": "cargo-flow-amr",
        "name": "CargoFlow 仓储搬运机器人",
        "description": "用于仓库货架间的自主搬运演示平台，支持路径规划和任务调度。",
        "base_price": Decimal("42800.00"),
        "capabilities": ["自主搬运", "路径规划", "任务调度"],
        "use_cases": ["仓储", "物流"],
        "specs": {"payload_kg": 300, "battery_hours": 10, "navigation": "激光 SLAM"},
    },
    {
        "sku": "RB-LAB-004",
        "slug": "lab-arm-mini",
        "name": "LabArm Mini 教学机械臂",
        "description": "面向机器人课程和实验台的六轴机械臂，支持视觉抓取与 Python 控制。",
        "base_price": Decimal("12800.00"),
        "capabilities": ["六轴控制", "视觉抓取", "Python SDK"],
        "use_cases": ["教育", "科研", "开发者"],
        "specs": {"axes": 6, "payload_kg": 2, "repeatability_mm": 0.1},
    },
    {
        "sku": "RB-VIS-005",
        "slug": "vision-rover-x",
        "name": "Vision Rover X 视觉移动平台",
        "description": "为算法验证设计的开放式移动底盘，集成视觉感知与 ROS 开发接口。",
        "base_price": Decimal("8999.00"),
        "capabilities": ["视觉感知", "ROS 2", "开放接口"],
        "use_cases": ["开发者", "科研", "教育"],
        "specs": {"compute": "12GB edge AI", "camera": "双目", "battery_hours": 4},
    },
    {
        "sku": "RB-PAT-006",
        "slug": "patrol-scout-s",
        "name": "Patrol Scout S 室内巡检机器人",
        "description": "面向办公楼和园区的轻量巡检终端，支持自主导航和异常点记录。",
        "base_price": Decimal("26800.00"),
        "capabilities": ["自主导航", "异常记录", "远程监控"],
        "use_cases": ["巡检", "园区"],
        "specs": {"battery_hours": 7, "navigation": "视觉 + 激光", "camera": "1080P"},
    },
    {
        "sku": "RB-EDU-007",
        "slug": "maker-bot-kit",
        "name": "Maker Bot 创客套件",
        "description": "适合社团和入门教学的模块化机器人套件，覆盖传感器与基础运动控制。",
        "base_price": Decimal("2499.00"),
        "capabilities": ["模块化拼装", "传感器实验", "Python SDK"],
        "use_cases": ["教育", "开发者"],
        "specs": {"compute": "入门控制板", "sensors": 8, "battery_hours": 2},
    },
    {
        "sku": "RB-SVC-008",
        "slug": "service-one",
        "name": "Service One 服务机器人",
        "description": "适合展厅和接待空间的交互机器人，支持语音引导与多点讲解。",
        "base_price": Decimal("35900.00"),
        "capabilities": ["语音交互", "自主导览", "多点讲解"],
        "use_cases": ["服务", "展厅"],
        "specs": {"screen_inches": 12, "battery_hours": 8, "navigation": "激光 SLAM"},
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
            elif payload["sku"] == "RB-SVC-008" and existing.use_cases != payload["use_cases"]:
                existing.use_cases = payload["use_cases"]
        await session.commit()
    print(f"Seeded {len(PRODUCTS)} products")


if __name__ == "__main__":
    asyncio.run(main())
