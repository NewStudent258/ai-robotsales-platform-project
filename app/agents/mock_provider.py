"""确定性 Mock Provider。

在真实 LLM 适配器接入前提供稳定行为，同时**完整驱动多轮与工具闭环**，
使编排器、工具契约与前端接入可以在无外部依赖的前提下被测试覆盖。

替换为真实模型时，只需另写一个实现 `AgentProvider` 协议的类，
编排器与业务链路无需改动。
"""

from decimal import Decimal, InvalidOperation

from app.agents.provider import AgentContext, AgentTurn, ToolCall
from app.agents.requirement import extract_quote_intent


def _decimal(value: object) -> Decimal:
    """把工具回传的金额字符串安全转成 Decimal；无法解析时按 0 处理。"""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


class MockAgentProvider:
    """基于规则的确定性 Provider。"""

    async def plan(self, context: AgentContext) -> AgentTurn:
        # 命中提示注入：只披露与拒绝，不改写任何业务事实。
        if context.injection_detected:
            return AgentTurn(
                answer=(
                    "我无法执行修改价格、跳过确认或改变订单状态的指令。"
                    "金额和订单状态均由平台的价格与订单服务按规则确定。"
                    "如果你的需求描述有变化，我可以重新为你选型和报价。"
                ),
                intent="policy_refusal",
                confidence=1.0,
            )

        # 冲突需求：先披露不确定性，请客户复核，而不是直接推进报价。
        if context.conflict_notes:
            return AgentTurn(
                answer=context.conflict_notes[0],
                intent="requirement_clarification",
                confidence=0.4,
                handoff_required=True,
                handoff_reason="需求存在冲突",
                missing_fields=context.gaps,
            )

        observations = {item.get("tool"): item for item in context.tool_observations}

        # 第一步：尚未检索过目录，先调用只读工具检索候选。
        if "search_products" not in observations:
            return AgentTurn(
                tool_calls=[ToolCall(name="search_products", arguments={"query": context.message})],
                intent="product_discovery",
                confidence=0.9,
            )

        search = observations["search_products"]
        products = (search.get("data") or {}).get("products") or []

        # 检索为空：明确告知并转人工，不编造产品。
        if not products:
            return AgentTurn(
                answer="暂时没有找到匹配的产品。请补充使用场景、预算或关键能力，我会继续筛选。",
                intent="product_discovery",
                confidence=0.3,
                handoff_required=True,
                handoff_reason="目录无匹配产品",
                missing_fields=context.gaps or ["使用场景", "预算", "关键能力"],
            )

        # 已有报价：进入确认环节，并给出待用户确认的动作。
        quote = observations.get("create_quote")
        if quote and quote.get("ok"):
            data = quote.get("data") or {}
            prepared = observations.get("prepare_order") or {}
            lines = [f"报价编号 {data.get('quote_number')}"]
            # 金额构成必须逐项披露，客户才能核对优惠与税额的依据。
            discount = _decimal(data.get("discount"))
            if discount > 0:
                lines.append(f"原价 {data.get('currency')} {data.get('subtotal')}")
                lines.append(f"折扣 −{data.get('discount')}")
            tax = _decimal(data.get("tax"))
            if tax > 0:
                lines.append(f"税额 {data.get('tax')}")
            lines.append(f"合计 {data.get('currency')} {data.get('total')}")
            if prepared.get("ok"):
                lines.append("订单已准备就绪，请你在页面上核对条款后确认创建。")
            return AgentTurn(
                answer="已生成正式报价：" + "；".join(lines) + "。",
                intent="quote_ready",
                confidence=0.95,
            )

        # 需求仍缺必填字段：针对缺口追问，不急于报价。
        if context.gaps:
            return AgentTurn(
                answer="我找到了几款可能适合的机器人。为了给出准确报价，请补充："
                + "、".join(context.gaps)
                + "。",
                intent="requirement_clarification",
                confidence=0.7,
                missing_fields=context.gaps,
            )

        # 需求完整且有候选：若已给出联系方式与目标产品，则生成正式报价。
        contacts = extract_quote_intent(context.message)
        if contacts.ready and products:
            product_id = contacts.product_id or products[0]["product_id"]
            # 数量可能在前几轮已经说过（如「采购2台」），必须回落到已累积的需求，
            # 否则会静默退回 1 台，给出偏低报价。
            quantity = contacts.quantity or context.requirement.get("quantity") or 1
            return AgentTurn(
                tool_calls=[
                    ToolCall(
                        name="create_quote",
                        arguments={
                            "product_id": product_id,
                            "quantity": quantity,
                            "customer_name": contacts.customer_name,
                            "customer_email": contacts.customer_email,
                        },
                    )
                ],
                intent="quote_generation",
                confidence=0.9,
            )

        # 需求完整但尚无联系方式：说明下一步需要什么。
        return AgentTurn(
            answer=(
                "根据你的场景我已筛选出候选方案。请提供联系人和邮箱，我就可以生成可复算的正式报价。"
            ),
            intent="product_recommendation",
            confidence=0.8,
            missing_fields=["联系人", "邮箱"],
        )
