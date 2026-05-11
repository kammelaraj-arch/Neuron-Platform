from __future__ import annotations

import asyncio
import os
import platform
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .log_collector import traces
from .models import DeviceRecord


router = APIRouter(tags=["meta"])

_BOOT_TIME = time.monotonic()


@router.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict:
    devices_total = await session.scalar(select(func.count()).select_from(DeviceRecord)) or 0
    devices_online = await session.scalar(
        select(func.count()).select_from(DeviceRecord).where(DeviceRecord.online.is_(True))
    ) or 0
    recent = await traces.recent(limit=1)
    return {
        "status": "ok",
        "edge_id": settings.edge_id,
        "site_id": settings.site_id,
        "uptime_s": round(time.monotonic() - _BOOT_TIME, 1),
        "devices_total": devices_total,
        "devices_online": devices_online,
        "last_trace_at": recent[0]["at"] if recent else None,
        "host": platform.node(),
        "now": datetime.now(timezone.utc).isoformat(),
    }


async def health_monitor_loop(stop_event: asyncio.Event) -> None:
    """Background coroutine: marks devices offline if no heartbeat in 90s."""
    from .db import SessionLocal
    while not stop_event.is_set():
        try:
            async with SessionLocal() as ses:
                rows = (await ses.execute(select(DeviceRecord).where(DeviceRecord.online.is_(True)))).scalars().all()
                cutoff = datetime.now(timezone.utc).timestamp() - 90
                for row in rows:
                    last = row.last_seen_at
                    if last is None:
                        continue
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last.timestamp() < cutoff:
                        row.online = False
                        await traces.record(ses, source="health_monitor", level="warning",
                                            message=f"device went offline (no heartbeat 90s)",
                                            device_dna=row.device_dna)
                await ses.commit()
        except Exception:  # noqa: BLE001 — never let monitor crash
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass
