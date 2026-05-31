import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import config as app_config
from app.database import Base, get_db
from app.main import app
from app.models.user import User, UserRole

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient_test",
)

# Raw DSN for asyncpg (strip the SQLAlchemy dialect prefix)
_ASYNCPG_DSN = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

_test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def rsa_keys():
    """Generate a test RSA keypair. Session-scoped so it runs once per test session."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest_asyncio.fixture(scope="session")
async def test_db():
    """Create all tables once for the test session, drop them at the end."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(test_db):
    """Direct DB session for fixture setup (inserting users, etc.)."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(rsa_keys, test_db):
    """AsyncClient wired to the test app with JWT public key patched."""
    private_pem, public_pem = rsa_keys

    original_key = app_config.settings.jwt_public_key
    app_config.settings.jwt_public_key = public_pem

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.redis = AsyncMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app_config.settings.jwt_public_key = original_key
    app.dependency_overrides.clear()
    await engine.dispose()


async def _truncate_all():
    """Open a fresh asyncpg connection (not pooled) to truncate tables."""
    conn = await asyncpg.connect(_ASYNCPG_DSN)
    try:
        await conn.execute(
            "TRUNCATE TABLE messages, sessions, disease_documents, diseases, units, enrollments, courses, users CASCADE"
        )
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def clean_tables(test_db):
    """Truncate all data before and after each test. Request this fixture in test files."""
    await _truncate_all()
    yield
    await _truncate_all()


def _make_token(user_id: uuid.UUID, private_pem: str) -> str:
    return jwt.encode({"sub": str(user_id)}, private_pem, algorithm="RS256")


@pytest_asyncio.fixture
async def professor(db_session, rsa_keys):
    """Insert a professor user and return (User, JWT token)."""
    private_pem, _ = rsa_keys
    user = User(
        id=uuid.uuid4(),
        google_uid=f"prof-{uuid.uuid4().hex}",
        email=f"professor-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Test Professor",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, _make_token(user.id, private_pem)


@pytest_asyncio.fixture
async def student(db_session, rsa_keys):
    """Insert a student user and return (User, JWT token)."""
    private_pem, _ = rsa_keys
    user = User(
        id=uuid.uuid4(),
        google_uid=f"stu-{uuid.uuid4().hex}",
        email=f"student-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student,
        is_verified=True,
        display_name="Test Student",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, _make_token(user.id, private_pem)
