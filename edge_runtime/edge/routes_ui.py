from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .command_router import bus
from .config import settings
from .db import get_session
from .log_collector import traces
from .models import AlarmRow, DeviceRecord
from .twin_cache import cache


_BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    devices = (await session.execute(select(DeviceRecord).order_by(DeviceRecord.first_seen_at.desc()))).scalars().all()
    alarms = (await session.execute(select(AlarmRow).where(AlarmRow.cleared_at.is_(None)).order_by(AlarmRow.raised_at.desc()).limit(20))).scalars().all()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "edge_id": settings.edge_id,
            "site_id": settings.site_id,
            "devices": devices,
            "alarms": alarms,
            "traces": await traces.recent(limit=30),
            "twins": await cache.all(),
            "commands": bus.history(limit=20),
        },
    )
