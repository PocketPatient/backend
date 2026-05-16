from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme"


settings = Settings()
