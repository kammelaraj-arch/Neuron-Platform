from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceRecord(Base):
    """Local device registry on the edge."""

    __tablename__ = "devices"

    device_dna: Mapped[str] = mapped_column(String(40), primary_key=True)
    device_type: Mapped[str] = mapped_column(String(40))
    compute_stable_id: Mapped[str | None] = mapped_column(String(120))
    hardware_revision: Mapped[str | None] = mapped_column(String(40))
    base_firmware_version: Mapped[str | None] = mapped_column(String(20))
    app_bundle_version: Mapped[str | None] = mapped_column(String(20))
    config_schema_version: Mapped[str | None] = mapped_column(String(20))
    last_seen_at: Mapped[datetime | None] = mapped_column()
    last_ip: Mapped[str | None] = mapped_column(String(64))
    online: Mapped[bool] = mapped_column(default=False)
    twin_controls_json: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(default=_now)


class TraceLogRow(Base):
    """Append-only trace log written by edge services and devices."""

    __tablename__ = "trace_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(default=_now, index=True)
    source: Mapped[str] = mapped_column(String(60), index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    device_dna: Mapped[str | None] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[dict | None] = mapped_column(JSON)


class AlarmRow(Base):
    """Active and historical alarms (per twin.alarm_model contract)."""

    __tablename__ = "alarms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    raised_at: Mapped[datetime] = mapped_column(default=_now, index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(120))
    device_dna: Mapped[str | None] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    acked_at: Mapped[datetime | None] = mapped_column()
    acked_by: Mapped[str | None] = mapped_column(String(80))
    cleared_at: Mapped[datetime | None] = mapped_column()
    latched: Mapped[bool] = mapped_column(default=True)
