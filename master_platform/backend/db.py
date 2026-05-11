from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _assert_isolated_db_url(url: str) -> None:
    """Refuse to start if NEURON_DB_URL points at ShitalEco's database.

    Neuron Platform must never share storage with the host monorepo. We
    look for ShitalEco-specific markers in the URL and fail fast if found.
    """
    forbidden_markers = (
        "shital",       # 'shital', 'shital_db', 'shitaleco_db', etc.
        "shitaleco",    # explicit
    )
    lowered = url.lower()
    for marker in forbidden_markers:
        if marker in lowered:
            raise RuntimeError(
                "NEURON_DB_URL must point at an isolated Neuron database; "
                f"refusing to start because the URL contains '{marker}': {url}"
            )


_assert_isolated_db_url(settings.db_url)
engine = create_async_engine(settings.db_url, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from . import models  # noqa: F401  (register models)
    from . import models_library  # noqa: F401  (register library item model)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
