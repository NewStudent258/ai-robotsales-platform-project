from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Robot Sales Platform"
    environment: str = "development"
    debug: bool = False
    database_url: str = "mysql+asyncmy://robot_sales:robot_sales@127.0.0.1:3306/robot_sales"
    auto_create_tables: bool = False
    cors_origins: str = "http://localhost:3000"
    llm_provider: str = "mock"
    llm_model: str | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"
    # 运营后台写权限令牌。为空时写接口一律拒绝（默认拒绝）。
    admin_api_token: str | None = None
    # 是否在 API 响应中回显报价访问令牌。默认关闭，仅在开发/测试中按需开启。
    expose_quote_token: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
