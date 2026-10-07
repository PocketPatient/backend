import os
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class ConfigError(RuntimeError):
    """Raised at startup when production config is missing or invalid.

    Messages name the offending env vars only — never their values — so the
    startup traceback in Cloud Logging cannot leak a secret.
    """


class Settings(BaseSettings):
    # extra="ignore": unknown keys in .env (e.g. GCP_PROJECT_ID) must not crash startup.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" enables the strict startup checks in validate_for_runtime().
    # Running on Cloud Run (K_SERVICE / CLOUD_RUN_JOB set) forces production
    # regardless, so a forgotten APP_ENV can't silently fall back to dev defaults.
    app_env: Literal["development", "test", "production"] = "development"

    # Dev defaults below are only usable outside production: validate_for_runtime()
    # requires DATABASE_URL / REDIS_URL to be explicitly set in production.
    database_url: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient", repr=False
    )
    # Injected separately from Secret Manager; overrides any password in DATABASE_URL.
    db_password: str = Field("", repr=False)
    redis_url: str = Field("redis://localhost:6379/0", repr=False)
    firebase_project_id: str = ""
    # Path to a service-account JSON *file* (e.g. a Cloud Run secret volume mount).
    firebase_credentials_path: str = ""
    allow_test_accounts: bool = False  # set True in .env for local dev only
    # Reviewer/demo switch: accept verified emails from ANY domain at login, not
    # just Rutgers (e.g. App Store reviewers using their own Apple ID). Allowed in
    # production — unlike ALLOW_TEST_ACCOUNTS it unlocks no dev-only behavior —
    # but it opens sign-up to everyone, so turn it off after review.
    allow_non_rutgers_accounts: bool = False
    # Cloud Storage bucket for disease-document uploads. Required in production:
    # Cloud Run's local disk is per-instance memory and not shared.
    gcs_upload_bucket: str = ""
    jwt_private_key: str = Field("", repr=False)
    jwt_public_key: str = ""
    gemini_api_key: str = Field("", repr=False)
    # Number of X-Forwarded-For entries appended by infrastructure we operate
    # (Cloud Run's front end = 1). 0 = ignore XFF entirely. See middleware/rate_limit.py.
    trusted_proxy_count: int = Field(0, ge=0)
    db_pool_size: int = Field(5, ge=1)
    db_max_overflow: int = Field(10, ge=0)

    @field_validator("jwt_private_key", "jwt_public_key", mode="before")
    @classmethod
    def _expand_newlines(cls, v: str) -> str:
        # Accept both a real multi-line PEM (Secret Manager -> env var) and the
        # single-line "\n"-escaped form used in .env files. Also tolerate
        # surrounding whitespace/quotes picked up when pasting into a secret.
        if not v:
            return v
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        return v.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")

    @property
    def is_production(self) -> bool:
        return (
            self.app_env == "production"
            or bool(os.getenv("K_SERVICE"))
            or bool(os.getenv("CLOUD_RUN_JOB"))
        )

    @property
    def sqlalchemy_url(self) -> URL:
        """DATABASE_URL with DB_PASSWORD applied.

        Returned as a URL object (not a string) so passwords containing
        @ : / % etc. need no manual escaping. Supports Cloud SQL's Unix socket
        form: postgresql+asyncpg://USER@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE
        """
        url = make_url(self.database_url)
        if self.db_password:
            url = url.set(password=self.db_password)
        return url

    def validate_for_runtime(self, component: Literal["api", "worker"]) -> None:
        """Fail fast if production config is missing or malformed. No-op outside production."""
        if not self.is_production:
            return
        problems: list[str] = []
        explicitly_set = self.model_fields_set

        if "database_url" not in explicitly_set:
            problems.append("DATABASE_URL is not set")
        else:
            try:
                url = self.sqlalchemy_url
            except Exception:
                problems.append("DATABASE_URL is not a valid SQLAlchemy URL")
            else:
                if url.drivername != "postgresql+asyncpg":
                    problems.append("DATABASE_URL must use the postgresql+asyncpg:// scheme")
                if not url.password:
                    problems.append("DB_PASSWORD is not set (and DATABASE_URL has no password)")
        if "redis_url" not in explicitly_set:
            problems.append("REDIS_URL is not set")
        if not self.gemini_api_key:
            problems.append("GEMINI_API_KEY is not set")
        if self.firebase_credentials_path:
            if not os.path.isfile(self.firebase_credentials_path):
                problems.append(
                    "FIREBASE_CREDENTIALS_PATH does not point to a readable file "
                    "(check the secret volume mount path)"
                )
        elif not self.firebase_project_id:
            problems.append("Set FIREBASE_CREDENTIALS_PATH or FIREBASE_PROJECT_ID")
        if self.allow_test_accounts:
            problems.append("ALLOW_TEST_ACCOUNTS must be false in production")

        if component == "api":
            problems.extend(self._jwt_key_problems())
            if not self.gcs_upload_bucket:
                problems.append("GCS_UPLOAD_BUCKET is not set")
            if self.trusted_proxy_count < 1:
                # Behind Cloud Run every request's socket peer is Google's front
                # end, so 0 collapses all clients into one rate-limit bucket.
                problems.append(
                    "TRUSTED_PROXY_COUNT must be >= 1 behind Cloud Run "
                    "(1 = direct ingress, 2 = external HTTPS load balancer)"
                )

        if problems:
            raise ConfigError(
                f"Invalid production configuration ({component}): " + "; ".join(problems)
            )

    def _jwt_key_problems(self) -> list[str]:
        from cryptography.hazmat.primitives import serialization

        if not self.jwt_private_key:
            return ["JWT_PRIVATE_KEY is not set"]
        if not self.jwt_public_key:
            return ["JWT_PUBLIC_KEY is not set"]
        try:
            private = serialization.load_pem_private_key(
                self.jwt_private_key.encode(), password=None
            )
        except Exception:
            # Deliberately drop the underlying exception: never echo key material.
            return ["JWT_PRIVATE_KEY is not a valid unencrypted PEM private key"]
        try:
            public = serialization.load_pem_public_key(self.jwt_public_key.encode())
        except Exception:
            return ["JWT_PUBLIC_KEY is not a valid PEM public key"]
        if private.public_key().public_numbers() != public.public_numbers():
            return ["JWT_PUBLIC_KEY does not match JWT_PRIVATE_KEY"]
        return []


settings = Settings()
