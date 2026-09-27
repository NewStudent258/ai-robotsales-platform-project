from dataclasses import dataclass

from app.models.product import Product


@dataclass(slots=True)
class AgentPlan:
    intent: str
    answer: str
    missing_fields: list[str]
    handoff_required: bool = False


class MockAgentProvider:
    """Deterministic provider used until a production LLM adapter is configured."""

    async def plan(self, message: str, products: list[Product]) -> AgentPlan:
        if not products:
            return AgentPlan(
                intent="product_discovery",
                answer="暂时没有找到完全匹配的产品。请补充使用场景、预算或需要的传感器，我会继续筛选。",
                missing_fields=["使用场景", "预算", "关键能力"],
                handoff_required=True,
            )
        return AgentPlan(
            intent="product_recommendation",
            answer="我找到了几款可能适合的机器人。下一步请确认部署场景、数量和预算，我再生成可复算报价。",
            missing_fields=["部署场景", "数量", "预算"],
        )
