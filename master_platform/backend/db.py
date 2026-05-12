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


engine = create_async_engine(settings.db_url, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from . import models  # noqa: F401  (register models)
    from . import models_library  # noqa: F401  (register library item model)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight in-place migrations for SQLite. Base.metadata.create_all
        # creates missing tables but never alters existing ones, so additive
        # column changes have to be applied by hand. Each migration is idempotent.
        await conn.run_sync(_apply_lightweight_migrations)


def _apply_lightweight_migrations(sync_conn) -> None:
    """Run additive SQLite migrations (idempotent)."""
    from sqlalchemy import text

    def _has_column(table: str, column: str) -> bool:
        rows = sync_conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in rows)

    def _has_table(table: str) -> bool:
        rows = sync_conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchall()
        return bool(rows)

    if not _has_table("edge_systems"):
        return  # fresh DB — create_all already gave us the right schema

    # 2026-05: hierarchy flexibility — Edge can attach directly to a Root
    # (skipping Node). node_id becomes nullable; root_id is added.
    if not _has_column("edge_systems", "root_id"):
        sync_conn.exec_driver_sql("ALTER TABLE edge_systems ADD COLUMN root_id VARCHAR(36)")

    # SQLite can't ALTER a column's NOT NULL flag. Recreate the table if
    # the live schema still marks node_id NOT NULL — otherwise the
    # wizard's "Skip Node, attach Edge to Root" path fails with an
    # integrity error.
    if _has_table("edge_systems"):
        info = sync_conn.exec_driver_sql("PRAGMA table_info(edge_systems)").fetchall()
        node_id_notnull = any(r[1] == "node_id" and r[3] == 1 for r in info)
        if node_id_notnull:
            sync_conn.exec_driver_sql("""
                CREATE TABLE edge_systems_new (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    node_id VARCHAR(36),
                    root_id VARCHAR(36),
                    name VARCHAR(120) NOT NULL,
                    site_id VARCHAR(120) NOT NULL,
                    address TEXT,
                    created_at DATETIME
                )
            """)
            sync_conn.exec_driver_sql("""
                INSERT INTO edge_systems_new (id, node_id, root_id, name, site_id, address, created_at)
                SELECT id, node_id, root_id, name, site_id, address, created_at FROM edge_systems
            """)
            sync_conn.exec_driver_sql("DROP TABLE edge_systems")
            sync_conn.exec_driver_sql("ALTER TABLE edge_systems_new RENAME TO edge_systems")

    # 2026-05: criticality + AI-agent-accessibility on code_functions.
    # Every catalogued function declares its blast radius and whether
    # the AI Agent may auto-invoke it.
    if _has_table("code_functions"):
        if not _has_column("code_functions", "criticality"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE code_functions ADD COLUMN criticality VARCHAR(20) NOT NULL DEFAULT 'nominal'"
            )
        if not _has_column("code_functions", "agent_accessible"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE code_functions ADD COLUMN agent_accessible BOOLEAN NOT NULL DEFAULT 1"
            )

    # 2026-05: WiFi primary/secondary on edge_groups. wifi_networks table
    # itself is created by Base.metadata.create_all when new.
    if _has_table("edge_groups"):
        if not _has_column("edge_groups", "primary_wifi_id"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE edge_groups ADD COLUMN primary_wifi_id VARCHAR(36)"
            )
        if not _has_column("edge_groups", "secondary_wifi_id"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE edge_groups ADD COLUMN secondary_wifi_id VARCHAR(36)"
            )


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
