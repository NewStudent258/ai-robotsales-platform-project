import re

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.schemas.product import ProductCreate


class ProductService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_products(self, query: str | None, page: int, page_size: int) -> tuple[list[Product], int]:
        filters = [Product.is_active.is_(True)]
        if query:
            term = f"%{query.strip()}%"
            filters.append(
                or_(Product.name.ilike(term), Product.description.ilike(term), Product.slug.ilike(term))
            )
        total = int((await self.session.scalar(select(func.count(Product.id)).where(*filters))) or 0)
        result = await self.session.scalars(
            select(Product)
            .where(*filters)
            .order_by(Product.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result), total

    async def get_product(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def create_product(self, payload: ProductCreate) -> Product:
        product = Product(**payload.model_dump())
        self.session.add(product)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def search_for_assistant(self, message: str, limit: int = 3) -> list[Product]:
        words = [word for word in message.replace(",", " ").split() if len(word) > 1]
        chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", message)
        for chunk in chinese_chunks:
            words.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
        words = list(dict.fromkeys(words))
        if not words:
            result = await self.session.scalars(
                select(Product).where(Product.is_active.is_(True)).order_by(Product.id.desc()).limit(limit)
            )
            return list(result)
        predicates = []
        for word in words[:8]:
            term = f"%{word}%"
            predicates.extend([Product.name.ilike(term), Product.description.ilike(term)])
        result = await self.session.scalars(
            select(Product).where(Product.is_active.is_(True), or_(*predicates)).limit(limit)
        )
        return list(result)
