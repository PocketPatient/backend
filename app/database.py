import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Echo every SQL statement only when explicitly enabled (dev/debug). Defaults
# off so production doesn't log every statement on top of the slow-query logger.
_SQL_ECHO = os.getenv("SQL_ECHO", "").lower() in ("1", "true", "yes")

engine = create_async_engine(settings.database_url, echo=_SQL_ECHO)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
