"""Agent 编排器。

职责边界（ARCH.md §1）：只负责路由、上下文裁剪、工具循环、预算、超时、
人工转接与审计，**不承载业务规则**。金额与订单状态始终由领域服务裁决。

单轮对话流程：
1. 载入或创建会话，持久化用户消息；
2. 确定性抽取需求、计算缺口、检测提示注入；
3. 在步数与工具调用预算内循环：Provider 决策 → 执行白名单工具 → 回灌观察结果；
4. 写入助手消息与工具轨迹，返回结构化响应。
"""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.mock_provider import MockAgentProvider
from app.agents.provider import (
    CONFIDENCE_THRESHOLD,
    MAX_AGENT_STEPS,
    MAX_TOOL_CALLS,
    AgentContext,
    AgentProvider,
    ToolRegistry,
)
from app.agents.requirement import RequirementExtractor, RequirementProfile, detect_injection
from app.agents.tools import build_default_registry
from app.models.commerce import Quote
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.assistant import AssistantMessageResponse, PendingAction, RecommendedProduct
from app.services.product_service import ProductService

# 喂给 Provider 的历史轮数上限，避免上下文无限增长。
HISTORY_LIMIT = 12


class AssistantService:
    """Provider 中立的编排边界。替换 LLM 只需注入不同的 Provider。"""

    def __init__(
        self,
        session: AsyncSession,
        provider: AgentProvider | None = None,
        registry: ToolRegistry | None = None,
    ):
        self.session = session
        self.products = ProductService(session)
        self.provider = provider or MockAgentProvider()
        self.registry = registry or build_default_registry()
        self.extractor = RequirementExtractor()

    async def respond(
        self, message: str, session_id: str | None, trace_id: str | None = None
    ) -> AssistantMessageResponse:
        conversation = await self._load_or_create_conversation(session_id)

        # 用户消息先落库：即使后续步骤失败，输入事实也不会丢失。
        self.session.add(
            ConversationMessage(
                conversation_id=conversation.id, role="user", content=message, trace_id=trace_id
            )
        )
        await self.session.commit()

        base = RequirementProfile.model_validate(conversation.requirement or {})
        extraction = self.extractor.extract(message, base)
        injection = detect_injection(message)

        conversation.requirement = extraction.profile.model_dump()
        if extraction.filled:
            conversation.requirement_version += 1
        if injection.detected:
            # 注入尝试本身不改变业务事实，但会留下人工可复核的痕迹。
            conversation.handoff_required = True
            conversation.handoff_reason = "检测到提示注入尝试"

        context = AgentContext(
            message=message,
            history=await self._load_history(conversation.id),
            requirement=conversation.requirement,
            gaps=extraction.gaps,
            conflict_notes=extraction.conflicts,
            injection_detected=injection.detected,
        )

        tool_observations: list[dict] = []
        tool_calls = 0
        answer: str | None = None
        intent = "product_recommendation"
        confidence = 1.0
        handoff_required = False
        handoff_reason: str | None = None
        missing_fields = extraction.gaps

        for step in range(MAX_AGENT_STEPS):
            context.step = step
            context.tool_observations = tool_observations
            turn = await self.provider.plan(context)
            intent = turn.intent
            confidence = turn.confidence
            missing_fields = turn.missing_fields or extraction.gaps
            if turn.handoff_required:
                handoff_required = True
                handoff_reason = turn.handoff_reason

            if turn.answer:
                answer = turn.answer
                break

            if not turn.tool_calls:
                # Provider 既没有说话也没有动作：安全停止，避免死循环。
                answer = "我需要更多信息才能继续，请补充你的使用场景和预算。"
                handoff_required = True
                handoff_reason = "Agent 未能形成有效决策"
                break

            for call in turn.tool_calls:
                if tool_calls >= MAX_TOOL_CALLS:
                    handoff_required = True
                    handoff_reason = "工具调用超出预算"
                    break
                result = await self.registry.call(call.name, call.arguments, self.session, context)
                tool_calls += 1
                tool_observations.append(
                    {
                        "tool": call.name,
                        "ok": result.ok,
                        "error_code": result.error_code,
                        "requires_confirmation": result.requires_confirmation,
                        "data": result.data,
                    }
                )
            if handoff_required and handoff_reason == "工具调用超出预算":
                answer = "这个请求需要更多步骤处理，我已为你转接人工同事。"
                break

        if answer is None:
            # 达到步数上限仍无结论：安全停止并转人工（AGENTS.md §5）。
            answer = "这个问题比较复杂，我已记录并为你转接人工同事继续跟进。"
            handoff_required = True
            handoff_reason = handoff_reason or "达到最大推理步数"

        if confidence < CONFIDENCE_THRESHOLD:
            handoff_required = True
            handoff_reason = handoff_reason or "置信度不足"

        if handoff_reason and not conversation.handoff_reason:
            conversation.handoff_reason = handoff_reason
        conversation.handoff_required = conversation.handoff_required or handoff_required

        quote_observation = next(
            (item for item in tool_observations if item["tool"] == "create_quote" and item["ok"]),
            None,
        )
        prepared = next(
            (item for item in tool_observations if item["tool"] == "prepare_order" and item["ok"]),
            None,
        )
        search = next(
            (item for item in tool_observations if item["tool"] == "search_products"), None
        )

        self.session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=answer,
                intent=intent,
                confidence=Decimal(str(confidence)),
                tool_calls=[self._audit_call(item) for item in tool_observations],
                trace_id=trace_id,
            )
        )
        await self.session.commit()

        return AssistantMessageResponse(
            session_id=conversation.session_id,
            intent=intent,
            answer=answer,
            missing_fields=missing_fields,
            recommendations=self._recommendations(search),
            quote=self._quote_payload(quote_observation),
            pending_action=self._pending_action(prepared),
            handoff_required=handoff_required,
            requirement=conversation.requirement,
        )

    async def _load_or_create_conversation(self, session_id: str | None) -> Conversation:
        if session_id:
            existing = await self.session.scalar(
                select(Conversation).where(Conversation.session_id == session_id)
            )
            if existing is not None:
                return existing
        conversation = Conversation(session_id=session_id or uuid4().hex)
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def _load_history(self, conversation_id: int) -> list[dict[str, str]]:
        rows = await self.session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.id.desc())
            .limit(HISTORY_LIMIT)
        )
        return [{"role": row.role, "content": row.content} for row in reversed(list(rows))]

    # 审计轨迹中永不记录的字段：凭证与载荷正文。
    _REDACTED_KEYS = frozenset({"access_token", "customer_email"})

    @classmethod
    def _audit_call(cls, item: dict) -> dict:
        """工具轨迹审计摘要：只记结构与结果，不落完整载荷。

        `quote_id` 与 `product_id` 是**引用标识**而非敏感载荷，必须保留：
        签发报价令牌时需要据此确认该报价确由本会话生成。
        凭证类字段名会被剔除，避免日志里出现 `access_token` 之类的字样。
        """
        data = item.get("data") or {}
        return {
            "tool": item["tool"],
            "ok": item["ok"],
            "error_code": item.get("error_code"),
            "requires_confirmation": item.get("requires_confirmation", False),
            "data_keys": sorted(key for key in data if key not in cls._REDACTED_KEYS),
            "quote_id": data.get("quote_id"),
            "product_id": data.get("product_id"),
        }

    @staticmethod
    def _recommendations(search: dict | None) -> list[RecommendedProduct]:
        if not search:
            return []
        products = (search.get("data") or {}).get("products") or []
        return [
            RecommendedProduct(
                product_id=product["product_id"],
                name=product["name"],
                reason="产品描述与当前需求存在关键词匹配，建议继续确认部署环境和配件兼容性。",
                base_price=product["base_price"],
            )
            for product in products
        ]

    @staticmethod
    def _quote_payload(observation: dict | None) -> dict | None:
        """把报价工具结果转成前端可直接跳转确认的结构。"""
        if not observation or not observation.get("ok"):
            return None
        data = observation.get("data") or {}
        return {
            "quote_id": data.get("quote_id"),
            "quote_number": data.get("quote_number"),
            "subtotal": data.get("subtotal"),
            # 折扣与税额由价格服务按规则计算，这里只做透传。
            "discount": data.get("discount"),
            "tax": data.get("tax"),
            "total": data.get("total"),
            "currency": data.get("currency"),
            "version": data.get("version"),
            "expires_at": data.get("expires_at"),
        }

    @staticmethod
    def _pending_action(observation: dict | None) -> PendingAction | None:
        """需要用户确认的写动作。订单永远不会由 Agent 自主创建。"""
        if not observation or not observation.get("ok"):
            return None
        data = observation.get("data") or {}
        return PendingAction(
            action="create_order",
            quote_id=data.get("quote_id"),
            requires_confirmation=True,
            message="请核对报价条款后确认创建订单。",
        )

    async def issue_quote_token(self, session_id: str, quote_id: int) -> str | None:
        """为会话内已生成的报价签发访问令牌。

        令牌不随对话响应批量回传，避免随日志或缓存扩散；
        调用方必须持有 `session_id`，且该报价确实由本会话生成，
        因此不能被用来读取任意报价。
        """
        conversation = await self.session.scalar(
            select(Conversation).where(Conversation.session_id == session_id)
        )
        if conversation is None:
            return None

        # 报价可能由更早的轮次生成，因此扫描本会话全部助手消息，
        # 而不只看最近一条。
        messages = await self.session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "assistant",
            )
        )
        issued: set[int] = set()
        for message in messages:
            for call in message.tool_calls or []:
                if call.get("tool") == "create_quote" and call.get("ok"):
                    call_quote_id = call.get("quote_id")
                    if isinstance(call_quote_id, int):
                        issued.add(call_quote_id)
        if quote_id not in issued:
            # 该报价并非本会话产出，拒绝签发。
            return None

        quote = await self.session.get(Quote, quote_id)
        return quote.access_token if quote else None
