"""Audit log retention — periodic prune of events older than the configured
keep-window. Runs as a lifespan task so a long-running Master never lets
its SQLite audit table grow unbounded.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from ..config import settings
from ..db import SessionLocal
from ..models import AuditEvent


_log = logging.getLogger("neuron.master.audit_retention")


async def prune_once() -> int:
    """Delete every audit event older than NEURON_AUDIT_KEEP_DAYS. Returns
    the number of deleted rows."""
    days = max(1, int(getattr(settings, "audit_keep_days", 90)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with SessionLocal() as session:
        result = await session.execute(
            delete(AuditEvent).where(AuditEvent.at < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)


async def retention_loop(stop_event: asyncio.Event) -> None:
    """Hourly prune loop. First run happens 60 s after start so a fresh
    boot doesn't slam the DB."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=60.0)
        return
    except asyncio.TimeoutError:
        pass
    while not stop_event.is_set():
        try:
            n = await prune_once()
            if n:
                _log.info("audit retention: pruned %d row(s)", n)
        except Exception as exc:  # noqa: BLE001
            _log.warning("audit retention failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600.0)
        except asyncio.TimeoutError:
            pass
