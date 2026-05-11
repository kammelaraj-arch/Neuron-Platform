from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """Edge Runtime ORM base."""


def _assert_isolated_db_url(url: str) -> None:
    forbidden = ("shital", "shitaleco", "neuron.db")
    lowered = url.lower()
    for marker in forbidden:
        if marker in lowered:
            raise RuntimeError(
                "EDGE_DB_URL must point at an isolated Edge database; "
                f"refusing to start because the URL contains '{marker}': {url}"
            )


_assert_isolated_db_url(settings.db_url)
engine = create_async_engine(settings.db_url, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
