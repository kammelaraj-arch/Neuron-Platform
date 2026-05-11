"""Trace + alarm collector. Persists to SQLite for retention and exposes
in-memory ring buffers for the dashboard UI.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AlarmRow, TraceLogRow


class TraceCollector:
    def __init__(self, ring_size: int = 1000) -> None:
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._lock = asyncio.Lock()

    async def record(
        self,
        session: AsyncSession,
        *,
        source: str,
        message: str,
        level: str = "info",
        device_dna: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        row = TraceLogRow(
            source=source,
            level=level,
            device_dna=device_dna,
            message=message,
            detail_json=detail,
        )
        session.add(row)
        await session.flush()
        entry = {
            "id": row.id,
            "at": row.at.isoformat() if row.at else datetime.now(timezone.utc).isoformat(),
            "source": source,
            "level": level,
            "device_dna": device_dna,
            "message": message,
            "detail_json": detail,
        }
        async with self._lock:
            self._ring.append(entry)

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._ring)[-limit:]


class AlarmCollector:
    async def raise_alarm(
        self,
        session: AsyncSession,
        *,
        severity: str,
        source: str,
        message: str,
        device_dna: str | None = None,
    ) -> AlarmRow:
        row = AlarmRow(
            severity=severity,
            source=source,
            message=message,
            device_dna=device_dna,
        )
        session.add(row)
        await session.flush()
        return row


traces = TraceCollector()
alarms = AlarmCollector()
