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


class QuoteItemRead(BaseModel):
    product_id: int
    product_name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


class QuoteRead(BaseModel):
    id: int
    quote_number: str
    # 仅在创建报价时返回一次；其余读取路径默认不回显访问令牌。
    access_token: str | None = None
    customer_name: str
    customer_email: str
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    status: str
    version: int
    expires_at: datetime | None
    items: list[QuoteItemRead]


class OrderRead(BaseModel):
    id: int
    order_number: str
    quote_id: int
    customer_name: str
    customer_email: str
    total: Decimal
    status: str
