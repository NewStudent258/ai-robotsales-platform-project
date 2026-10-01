import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["AUTO_CREATE_TABLES"] = "false"
os.environ["DEBUG"] = "true"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
async def session_factory():
    """内存库会话工厂，供测试直接断言持久化事实。

    注意：`:memory:` 在 StaticPool 下所有会话共享同一条连接，
    因此无法真实模拟并发写入；并发用例请使用 `client_factory`。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
async def client_factory(tmp_path):
    """文件型 SQLite + 真实连接池，用于并发用例。

    每个请求获得独立连接与会话，因此能复现真实的多连接竞态，
    而不是共享单连接带来的假象。
    """
    database = tmp_path / "concurrency.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}", pool_size=10, max_overflow=20)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
