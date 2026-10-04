"""对话与需求的持久化模型。

ARCH.md §5 要求 Conversation 具备 `conversation_id`、客户、渠道、状态、trace、
摘要与保留期。此前 `session_id` 只在响应中生成、从不落库，导致助手无法多轮记忆，
也无法做审计与断点续跑。本模块补齐这一事实层。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Conversation(TimestampMixin, Base):
    """一次客户会话。`session_id` 是外部可见标识，主键仅供内部关联。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), default="web")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    # 结构化需求快照：随每轮对话覆盖更新，是 S1 需求采集的确定性产物。
    requirement: Mapped[dict] = mapped_column(JSON, default=dict)
    # 已通过校验的必填字段，用于计算缺口。
    requirement_version: Mapped[int] = mapped_column(Integer, default=0)
    handoff_required: Mapped[bool] = mapped_column(default=False)
    handoff_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ConversationMessage(Base):
    """单条对话消息。写入即事实，用于多轮上下文与审计回放。"""

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # user / assistant / tool，与 LLM 消息角色对齐，便于直接喂给 Provider。
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # 助手消息记录其决策意图与置信度，供低置信度转人工判断。
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # 工具调用轨迹：名称、参数摘要、结果摘要、错误码。
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
