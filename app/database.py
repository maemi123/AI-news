from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=settings.debug, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_runtime_migrations(conn)


async def _apply_runtime_migrations(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(podcast_settings)"))
    columns = {row[1] for row in result.fetchall()}
    if 'podcast_channel' not in columns:
        await conn.execute(
            text("ALTER TABLE podcast_settings ADD COLUMN podcast_channel VARCHAR(32) NOT NULL DEFAULT 'built_in'")
        )
