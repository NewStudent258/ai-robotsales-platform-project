from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssistantMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class RecommendedProduct(BaseModel):
    product_id: int
    name: str
    reason: str
    base_price: str


class AssistantQuote(BaseModel):
    """助手生成的正式报价摘要。

    只暴露跳转确认所需字段；`access_token` 不在此回传，
    避免凭证随对话响应扩散到日志或浏览器缓存。
    """

    quote_id: int
    quote_number: str
    subtotal: str | None = None
    discount: str | None = None
    tax: str | None = None
    total: str
    currency: str
    version: int
    expires_at: str | None = None


class PendingAction(BaseModel):
    """需要用户显式确认的写动作。Agent 不会自主执行下单。"""

    action: str
    quote_id: int | None = None
    requires_confirmation: bool = True
    message: str


class QuoteTokenRequest(BaseModel):
    """按需签发报价访问令牌的请求。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=64)
    quote_id: int = Field(ge=1)


class QuoteTokenResponse(BaseModel):
    quote_id: int
    access_token: str


class AssistantMessageResponse(BaseModel):
    session_id: str
    intent: str
    answer: str
    missing_fields: list[str] = Field(default_factory=list)
    recommendations: list[RecommendedProduct] = Field(default_factory=list)
    # 已生成报价时给出，前端据此直接接通报价确认流程。
    quote: AssistantQuote | None = None
    pending_action: PendingAction | None = None
    handoff_required: bool = False
    # 当前结构化需求快照，便于前端回显与客户修正。
    requirement: dict[str, Any] = Field(default_factory=dict)
