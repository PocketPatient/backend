from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme"
    firebase_project_id: str = ""
    firebase_credentials_path: str = ""
    jwt_private_key: str = ""
    jwt_public_key: str = ""

    @field_validator("jwt_private_key", "jwt_public_key", mode="before")
    @classmethod
    def _expand_newlines(cls, v: str) -> str:
        return v.replace("\\n", "\n") if v else v


settings = Settings()
