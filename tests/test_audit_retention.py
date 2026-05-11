"""Audit retention prune."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
async def initialised_master(isolated_master):
    from backend.db import init_db
    await init_db()
    yield isolated_master


async def test_prune_drops_old_events_only(initialised_master, monkeypatch):
    from backend.db import SessionLocal
    from backend.models import AuditEvent
    from backend.config import settings
    from backend.security.audit_retention import prune_once

    # Force a 7-day window for the test.
    monkeypatch.setattr(settings, "audit_keep_days", 7, raising=False)

    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        s.add(AuditEvent(actor="a", action="recent", outcome="ok",
                         at=now - timedelta(days=1)))
        s.add(AuditEvent(actor="a", action="old", outcome="ok",
                         at=now - timedelta(days=30)))
        s.add(AuditEvent(actor="a", action="ancient", outcome="ok",
                         at=now - timedelta(days=400)))
        await s.commit()

    deleted = await prune_once()
    assert deleted == 2  # 30d + 400d removed; 1d kept

    async with SessionLocal() as s:
        from sqlalchemy import select, func
        n = await s.scalar(select(func.count()).select_from(AuditEvent))
        assert n == 1


async def test_prune_zero_when_empty(initialised_master):
    from backend.security.audit_retention import prune_once
    assert await prune_once() == 0
