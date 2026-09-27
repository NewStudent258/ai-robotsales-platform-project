from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    base_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    use_cases: Mapped[list] = mapped_column(JSON, default=list)
    specs: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ProductCompatibility(TimestampMixin, Base):
    __tablename__ = "product_compatibilities"
    __table_args__ = (UniqueConstraint("product_id", "compatible_product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    compatible_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    relation: Mapped[str] = mapped_column(String(32), default="compatible")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
