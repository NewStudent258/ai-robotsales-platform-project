from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class QuoteItemRequest(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=10000)


class QuoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(min_length=1, max_length=120)
    customer_email: str = Field(min_length=3, max_length=200, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    items: list[QuoteItemRequest] = Field(min_length=1)
    # 仅用于规则匹配的上下文，不参与金额计算；金额仍由配置项决定。
    industry: str | None = Field(default=None, max_length=64)


class QuoteReviseRequest(QuoteCreateRequest):
    """重新报价请求。除报价要素外必须回传读到的版本号做乐观锁。"""

    expected_version: int = Field(ge=1)


class AppliedRuleRead(BaseModel):
    """本报价实际生效的规则，供前端展示「优惠依据」。"""

    code: str
    name: str
    kind: str
    version: int
    priority: int
    amount: Decimal
    detail: str


class QuoteItemRead(BaseModel):
    product_id: int
    product_name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


class QuoteVersionRead(BaseModel):
    """版本链上的一个历史版本。"""

    id: int
    quote_number: str
    version: int
    status: str
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    is_current: bool = False


class QuoteRead(BaseModel):
    id: int
    quote_number: str
    # 仅在创建报价时返回一次；其余读取路径默认不回显访问令牌。
    access_token: str | None = None
    customer_name: str
    customer_email: str
    currency: str
    subtotal: Decimal
    discount: Decimal = Decimal("0")
    tax: Decimal
    total: Decimal
    status: str
    version: int
    expires_at: datetime | None
    items: list[QuoteItemRead]
    # 生效规则与版本链，使金额可解释、历史可追溯。
    applied_rules: list[AppliedRuleRead] = Field(default_factory=list)
    versions: list[QuoteVersionRead] = Field(default_factory=list)


class OrderRead(BaseModel):
    id: int
    order_number: str
    quote_id: int
    customer_name: str
    customer_email: str
    total: Decimal
    status: str
    # 当前状态下允许迁移到的目标，便于运营端与测试发现合法动作。
    allowed_transitions: list[str] = Field(default_factory=list)


class OrderTransitionRequest(BaseModel):
    """订单状态迁移请求。"""

    model_config = ConfigDict(extra="forbid")

    # 目标状态取值校验交给状态机，以返回语义明确的 ORDER_INVALID_TRANSITION。
    to_status: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=500)
    # 可选并发保护：调用方回传其读到的状态，不一致即拒绝。
    expected_status: str | None = Field(default=None, max_length=32)
