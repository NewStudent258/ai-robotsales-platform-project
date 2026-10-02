from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import assistant, commerce, products
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.db.base import Base
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_tables:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 错误封套与 trace_id 中间件在 CORS 之后注册，确保它包住所有路由与异常。
install_error_handlers(app)
app.include_router(products.router, prefix="/api/v1")
app.include_router(assistant.router, prefix="/api/v1")
app.include_router(commerce.router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/sales", include_in_schema=False)
async def sales_frontend() -> FileResponse:
    return FileResponse(frontend_dir / "sales.html")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}
