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

    # 2026-05: lock-with-pincode on edge_groups for tested configs.
    if _has_table("edge_groups"):
        if not _has_column("edge_groups", "lock_pin_hash"):
            sync_conn.exec_driver_sql("ALTER TABLE edge_groups ADD COLUMN lock_pin_hash TEXT")
        if not _has_column("edge_groups", "locked_at"):
            sync_conn.exec_driver_sql("ALTER TABLE edge_groups ADD COLUMN locked_at DATETIME")
        if not _has_column("edge_groups", "locked_by"):
            sync_conn.exec_driver_sql("ALTER TABLE edge_groups ADD COLUMN locked_by VARCHAR(120)")

    # 2026-05: corrected hierarchy (docs/wizard_spec.md) — a Group's
    # Compute→Boards→Components bundle attaches to Root (master),
    # Node (gateway) or Edge (edge). Same shape for all three, so
    # use a polymorphic (parent_kind, parent_id) pair instead of
    # one FK column per parent type. Lift NOT NULL on the legacy
    # edge_id column and backfill the new fields from it.
    if _has_table("edge_groups"):
        if not _has_column("edge_groups", "parent_kind"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE edge_groups ADD COLUMN parent_kind VARCHAR(20) NOT NULL DEFAULT 'edge'"
            )
        if not _has_column("edge_groups", "parent_id"):
            sync_conn.exec_driver_sql("ALTER TABLE edge_groups ADD COLUMN parent_id VARCHAR(36)")
        # Lift NOT NULL on edge_id if still present (SQLite table rebuild).
        info = sync_conn.exec_driver_sql("PRAGMA table_info(edge_groups)").fetchall()
        edge_notnull = any(r[1] == "edge_id" and r[3] == 1 for r in info)
        if edge_notnull:
            cols_decl: list[str] = []
            col_names: list[str] = []
            for cid, name, ctype, notnull, dflt, pk in info:
                col_names.append(name)
                parts = [name, ctype or "TEXT"]
                if pk:
                    parts.append("PRIMARY KEY")
                if notnull and name != "edge_id":
                    parts.append("NOT NULL")
                if dflt is not None:
                    parts.append(f"DEFAULT {dflt}")
                cols_decl.append(" ".join(parts))
            cols_sql = ", ".join(cols_decl)
            cols_csv = ", ".join(col_names)
            sync_conn.exec_driver_sql(f"CREATE TABLE edge_groups_new ({cols_sql})")
            sync_conn.exec_driver_sql(f"INSERT INTO edge_groups_new ({cols_csv}) SELECT {cols_csv} FROM edge_groups")
            sync_conn.exec_driver_sql("DROP TABLE edge_groups")
            sync_conn.exec_driver_sql("ALTER TABLE edge_groups_new RENAME TO edge_groups")
        # Backfill polymorphic parent from legacy edge_id.
        sync_conn.exec_driver_sql(
            "UPDATE edge_groups SET parent_id = edge_id, parent_kind = 'edge' "
            "WHERE parent_id IS NULL AND edge_id IS NOT NULL"
        )

    # 2026-05: spec-driven additions (docs/wizard_spec.md) — role,
    # asset id, factory/line/machine hierarchy, industrial protocols,
    # local rules engine, telemetry channels, NTP, Bluetooth.
    if _has_table("edge_groups"):
        for col, sql_type in [
            ("role", "VARCHAR(20) NOT NULL DEFAULT 'edge'"),
            ("asset_id", "VARCHAR(120)"),
            ("factory", "VARCHAR(120)"),
            ("line", "VARCHAR(120)"),
            ("machine", "VARCHAR(120)"),
            ("protocols_json", "JSON"),
            ("rules_json", "JSON"),
            ("telemetry_json", "JSON"),
            ("ntp_servers_json", "JSON"),
            ("bluetooth_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
            ("lifecycle_status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
            ("decommissioned_at", "DATETIME"),
            ("local_ip", "VARCHAR(60)"),
            ("external_ip", "VARCHAR(60)"),
            ("hostname", "VARCHAR(200)"),
        ]:
            if not _has_column("edge_groups", col):
                sync_conn.exec_driver_sql(f"ALTER TABLE edge_groups ADD COLUMN {col} {sql_type}")

    # 2026-05: API endpoint + Fernet-encrypted key per ComponentInstance
    # for direct-API devices (Tapo plugs, IP cameras, Hue bulbs).
    if _has_table("component_instances"):
        if not _has_column("component_instances", "api_endpoint"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN api_endpoint VARCHAR(400)"
            )
        if not _has_column("component_instances", "api_key_encrypted"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN api_key_encrypted TEXT"
            )
        if not _has_column("component_instances", "vendor_account_id"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN vendor_account_id VARCHAR(36)"
            )

    # 2026-05: SSH / first-boot deployment credentials on edge_groups.
    # Sensitive fields stored Fernet-encrypted via secret_crypto.
    if _has_table("edge_groups"):
        for col, sql_type, default in [
            ("ssh_host", "VARCHAR(200)", None),
            ("ssh_port", "INTEGER NOT NULL DEFAULT 22", None),
            ("ssh_username", "VARCHAR(120)", None),
            ("ssh_password_encrypted", "TEXT", None),
            ("ssh_private_key_encrypted", "TEXT", None),
            ("sudo_password_encrypted", "TEXT", None),
            ("mdns_hostname", "VARCHAR(200)", None),
            ("ssh_enabled", "BOOLEAN NOT NULL DEFAULT 1", None),
        ]:
            if not _has_column("edge_groups", col):
                sync_conn.exec_driver_sql(f"ALTER TABLE edge_groups ADD COLUMN {col} {sql_type}")

    # 2026-05: board-pin assignment + functional label on
    # component_instances so each output channel of a board carries a
    # named function ("stirring", "tilt-z", "mixer") + asset id.
    if _has_table("component_instances"):
        if not _has_column("component_instances", "board_pin"):
            sync_conn.exec_driver_sql("ALTER TABLE component_instances ADD COLUMN board_pin VARCHAR(40)")
        if not _has_column("component_instances", "function_label"):
            sync_conn.exec_driver_sql("ALTER TABLE component_instances ADD COLUMN function_label VARCHAR(80)")
        if not _has_column("component_instances", "asset_id"):
            sync_conn.exec_driver_sql("ALTER TABLE component_instances ADD COLUMN asset_id VARCHAR(60)")

    # 2026-05: per-instance risk + failsafe on component_instances. The
    # local brain enforces these autonomously when the edge/master link
    # is dead — they are the last line of defence.
    if _has_table("component_instances"):
        if not _has_column("component_instances", "risk_level"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN risk_level VARCHAR(20) NOT NULL DEFAULT 'nominal'"
            )
        if not _has_column("component_instances", "risk_types_json"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN risk_types_json JSON"
            )
        if not _has_column("component_instances", "failsafe_action"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN failsafe_action VARCHAR(20)"
            )
        if not _has_column("component_instances", "failsafe_value_json"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN failsafe_value_json JSON"
            )
        if not _has_column("component_instances", "disconnect_grace_seconds"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN disconnect_grace_seconds INTEGER NOT NULL DEFAULT 30"
            )
        if not _has_column("component_instances", "watchdog_ms"):
            sync_conn.exec_driver_sql(
                "ALTER TABLE component_instances ADD COLUMN watchdog_ms INTEGER NOT NULL DEFAULT 1000"
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
