from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import MockAgentProvider
from app.schemas.assistant import AssistantMessageResponse, RecommendedProduct
from app.services.product_service import ProductService


class AssistantService:
    """Provider-neutral orchestration boundary for the future LLM adapter."""

    def __init__(self, session: AsyncSession):
        self.products = ProductService(session)
        self.provider = MockAgentProvider()

    async def respond(self, message: str, session_id: str | None) -> AssistantMessageResponse:
        current_session = session_id or str(uuid4())
        products = await self.products.search_for_assistant(message)
        plan = await self.provider.plan(message, products)
        recommendations = [
            RecommendedProduct(
                product_id=product.id,
                name=product.name,
                reason="产品描述与当前需求存在关键词匹配，建议继续确认部署环境和配件兼容性。",
                base_price=str(product.base_price),
            )
            for product in products
        ]
        return AssistantMessageResponse(
            session_id=current_session,
            intent=plan.intent,
            answer=plan.answer,
            missing_fields=plan.missing_fields,
            recommendations=recommendations,
            handoff_required=plan.handoff_required,
        )
