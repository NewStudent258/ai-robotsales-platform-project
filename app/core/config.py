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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
