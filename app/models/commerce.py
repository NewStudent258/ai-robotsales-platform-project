from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Quote(TimestampMixin, Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    quote_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    access_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_email: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    # 折扣额（正数表示减免多少）。旧数据为 0，保持向后兼容。
    discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    version: Mapped[int] = mapped_column(default=1)
    # 版本谱系：同一报价经重新报价形成链，root_quote_id 指向首版。
    # 首版自指（root_quote_id == id 在写入时由服务赋值）。
    root_quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quotes.id"), nullable=True, index=True
    )
    # 被取代它的新版本 ID（旧版被重新报价后写入），便于双向追溯。
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class QuoteItem(Base):
    __tablename__ = "quote_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(200))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    quantity: Mapped[int] = mapped_column(default=1)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_email: Mapped[str] = mapped_column(String(200))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(32), default="CREATING", index=True)


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("operation", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
