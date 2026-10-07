"""Production config validation (Cloud Run deploy readiness)."""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import ConfigError, Settings


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv, pub


_PRIV, _PUB = _keypair()


@pytest.fixture
def sa_file(tmp_path):
    path = tmp_path / "sa.json"
    path.write_text("{}")
    return str(path)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for var in (
        "K_SERVICE", "CLOUD_RUN_JOB", "APP_ENV", "DATABASE_URL", "DB_PASSWORD",
        "REDIS_URL", "GEMINI_API_KEY", "JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY",
        "FIREBASE_CREDENTIALS_PATH", "FIREBASE_PROJECT_ID", "ALLOW_TEST_ACCOUNTS",
        "GCS_UPLOAD_BUCKET", "ALLOW_NON_RUTGERS_ACCOUNTS", "TRUSTED_PROXY_COUNT",
    ):
        monkeypatch.delenv(var, raising=False)


def _prod(sa_file: str, **overrides) -> Settings:
    values = dict(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://app@/pocketpatient?host=/cloudsql/p:us-east1:i",
        db_password="s3cr3t-db-pw",
        redis_url="redis://10.0.0.3:6379/0",
        gemini_api_key="gemini-secret-value",
        jwt_private_key=_PRIV,
        jwt_public_key=_PUB,
        firebase_credentials_path=sa_file,
        gcs_upload_bucket="pp-uploads",
        trusted_proxy_count=1,
    )
    values.update(overrides)
    return Settings(**values)


def test_valid_production_config_passes(sa_file):
    _prod(sa_file).validate_for_runtime("api")


def test_development_skips_checks():
    Settings(_env_file=None).validate_for_runtime("api")


def test_cloud_run_forces_production(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "pocketpatient-api")
    s = Settings(_env_file=None)
    assert s.is_production
    with pytest.raises(ConfigError):
        s.validate_for_runtime("api")


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"gemini_api_key": ""}, "GEMINI_API_KEY"),
        ({"db_password": ""}, "DB_PASSWORD"),
        ({"jwt_private_key": ""}, "JWT_PRIVATE_KEY"),
        ({"jwt_public_key": ""}, "JWT_PUBLIC_KEY"),
        ({"jwt_public_key": _keypair()[1]}, "does not match"),
        ({"jwt_private_key": "not a pem"}, "JWT_PRIVATE_KEY"),
        ({"firebase_credentials_path": "/nope/sa.json"}, "FIREBASE_CREDENTIALS_PATH"),
        ({"firebase_credentials_path": ""}, "FIREBASE_PROJECT_ID"),
        ({"allow_test_accounts": True}, "ALLOW_TEST_ACCOUNTS"),
        ({"gcs_upload_bucket": ""}, "GCS_UPLOAD_BUCKET"),
        ({"trusted_proxy_count": 0}, "TRUSTED_PROXY_COUNT"),
    ],
)
def test_missing_or_bad_production_values_fail(sa_file, overrides, expected):
    with pytest.raises(ConfigError) as exc:
        _prod(sa_file, **overrides).validate_for_runtime("api")
    assert expected in str(exc.value)


def test_unset_database_and_redis_urls_fail_instead_of_using_dev_defaults(sa_file):
    s = Settings(
        _env_file=None, app_env="production", db_password="x", gemini_api_key="g",
        jwt_private_key=_PRIV, jwt_public_key=_PUB, firebase_credentials_path=sa_file,
    )
    with pytest.raises(ConfigError) as exc:
        s.validate_for_runtime("api")
    assert "DATABASE_URL is not set" in str(exc.value)
    assert "REDIS_URL is not set" in str(exc.value)


def test_worker_does_not_require_jwt_keys_or_bucket(sa_file):
    _prod(
        sa_file, jwt_private_key="", jwt_public_key="", gcs_upload_bucket="",
        trusted_proxy_count=0,
    ).validate_for_runtime("worker")


def test_non_rutgers_demo_flag_is_allowed_in_production(sa_file):
    _prod(sa_file, allow_non_rutgers_accounts=True).validate_for_runtime("api")


def test_error_message_never_contains_secret_values(sa_file):
    s = _prod(sa_file, jwt_public_key="", allow_test_accounts=True)
    with pytest.raises(ConfigError) as exc:
        s.validate_for_runtime("api")
    msg = str(exc.value)
    for secret in ("s3cr3t-db-pw", "gemini-secret-value", "PRIVATE KEY"):
        assert secret not in msg
    assert "s3cr3t-db-pw" not in repr(s)
    assert "gemini-secret-value" not in repr(s)


def test_db_password_applied_to_cloud_sql_socket_url(sa_file):
    url = _prod(sa_file, db_password="p@ss:w/rd%").sqlalchemy_url
    assert url.password == "p@ss:w/rd%"
    assert url.query["host"] == "/cloudsql/p:us-east1:i"
    assert url.database == "pocketpatient"


@pytest.mark.parametrize(
    "raw",
    [
        _PRIV,  # real newlines (Secret Manager -> env var)
        _PRIV.replace("\n", "\\n"),  # escaped single-line (.env style)
        '"' + _PRIV.replace("\n", "\\n") + '"\n',  # pasted with quotes + trailing newline
        _PRIV.replace("\n", "\r\n"),  # CRLF
    ],
)
def test_pem_newline_forms_all_load(sa_file, raw):
    _prod(sa_file, jwt_private_key=raw).validate_for_runtime("api")
