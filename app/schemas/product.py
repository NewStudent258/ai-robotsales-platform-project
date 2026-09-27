from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductBase(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    slug: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    description: str
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    base_price: Decimal = Field(gt=0)
    image_url: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    specs: dict = Field(default_factory=dict)


class ProductCreate(ProductBase):
    pass


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class ProductList(BaseModel):
    items: list[ProductRead]
    total: int
    page: int
    page_size: int
