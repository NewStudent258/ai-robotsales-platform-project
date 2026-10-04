"""Agent Provider 抽象与工具契约。

本模块定义 Agent 编排层与模型之间的**唯一**边界（ARCH.md §1）：

- `AgentProvider` 是把对话上下文映射为「说话 + 工具调用」的纯策略接口；
- `ToolRegistry` 是受控工具白名单，写工具必须显式声明是否需用户确认；
- 编排器（`assistant_service`）负责循环、预算、审计与错误处理。

设计约束（AGENTS.md §5）：
- Agent 只能调用注册工具，禁止动态拼接未声明工具；
- 金额与订单状态永远由领域服务裁决，工具只做转译；
- 每轮对话有最大步数与最大工具调用数，达到上限安全停止。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

# 单轮对话的最大推理步数与工具调用次数。达到上限必须安全停止并转人工。
MAX_AGENT_STEPS = 4
MAX_TOOL_CALLS = 6
# 低于该置信度视为不可直接作答，需要追问或转人工。
CONFIDENCE_THRESHOLD = 0.55

# 工具风险分级：read 无副作用；write_local 仅创建草稿类对象；
# write_commit 会产生业务承诺（下单、状态迁移），必须由用户确认。
TOOL_RISK_READ = "read"
TOOL_RISK_WRITE_LOCAL = "write_local"
TOOL_RISK_WRITE_COMMIT = "write_commit"


@dataclass(slots=True)
class ToolCall:
    """一次工具调用请求。参数由编排器校验后才执行。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """工具执行结果。`data` 只放可安全回传给模型与前端的事实。"""

    name: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None
    # 需要用户显式确认才能执行的写操作，会带上该标记而**不真正执行**。
    requires_confirmation: bool = False

    def summary(self) -> dict[str, Any]:
        """审计用的结果摘要，避免把完整载荷写入日志。"""
        return {
            "tool": self.name,
            "ok": self.ok,
            "error_code": self.error_code,
            "requires_confirmation": self.requires_confirmation,
            "keys": sorted(self.data.keys()),
        }


@dataclass(slots=True)
class AgentTurn:
    """Provider 产出的一步决策：要么说话，要么调用工具。"""

    answer: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    intent: str = "product_recommendation"
    confidence: float = 1.0
    handoff_required: bool = False
    handoff_reason: str | None = None
    missing_fields: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentContext:
    """喂给 Provider 的上下文。只包含已持久化的事实与结构化需求。"""

    message: str
    history: list[dict[str, str]] = field(default_factory=list)
    requirement: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    conflict_notes: list[str] = field(default_factory=list)
    injection_detected: bool = False
    # 上一轮工具结果的精简视图，供模型决定下一步。
    tool_observations: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0


class AgentProvider(Protocol):
    """把上下文映射为一步决策的策略接口。

    真实 LLM 适配器与 `MockAgentProvider` 均实现该协议；
    编排器只依赖本协议，因此替换模型不需要改动业务链路。
    """

    async def plan(self, context: AgentContext) -> AgentTurn: ...


@dataclass(slots=True)
class ToolSpec:
    """工具声明。`handler` 签名固定为 (session, arguments, context) -> ToolResult。"""

    name: str
    description: str
    risk: str
    handler: Any
    requires_confirmation: bool = False


class ToolRegistry:
    """受控工具白名单。

    编排器只能通过 `registry.call()` 执行工具，未注册名称一律拒绝，
    从而杜绝模型幻觉出的工具名产生副作用。
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def describe(self) -> list[dict[str, Any]]:
        """给模型/文档用的工具清单。"""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "risk": spec.risk,
                "requires_confirmation": spec.requires_confirmation,
            }
            for spec in self._specs.values()
        ]

    async def call(
        self, name: str, arguments: dict[str, Any], session: AsyncSession, context: AgentContext
    ) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                name=name,
                ok=False,
                error_code="TOOL_NOT_ALLOWED",
                message="该工具未注册，拒绝执行。",
            )
        return await spec.handler(session, arguments, context)
