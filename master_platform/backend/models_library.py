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


class LibraryItemRow(Base):
    """DB-backed library item.

    The full manifest JSON is stored in ``manifest_json`` so the schema
    can evolve without DB migrations. ``stable_id`` and ``library`` are
    duplicated as columns to allow indexed queries.
    """

    __tablename__ = "library_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    stable_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    library: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    vendor: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column()
