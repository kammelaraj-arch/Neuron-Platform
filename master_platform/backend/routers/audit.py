from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas
from ..db import get_session
from ..models import APIKey, AuditEvent
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/audit", tags=["audit"])


def _audit_query(stmt, *, actor, action, since, until):
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if since:
        stmt = stmt.where(AuditEvent.at >= since)
    if until:
        stmt = stmt.where(AuditEvent.at <= until)
    return stmt


@router.get("", response_model=list[schemas.AuditEventOut])
async def query_audit(
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("audit:read")),
):
    stmt = _audit_query(
        select(AuditEvent).order_by(AuditEvent.at.desc()).limit(limit),
        actor=actor, action=action, since=since, until=until,
    )
    res = await session.execute(stmt)
    return list(res.scalars())


@router.get("/export.csv")
async def export_audit_csv(
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=10000, ge=1, le=100000),
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("audit:read")),
):
    """Streaming CSV export of audit events. Same filters as the JSON
    endpoint but the limit is wider so a full audit dump is one call.
    """
    stmt = _audit_query(
        select(AuditEvent).order_by(AuditEvent.at.desc()).limit(limit),
        actor=actor, action=action, since=since, until=until,
    )
    rows = (await session.execute(stmt)).scalars().all()

    def gen():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "id", "at", "actor", "actor_kind", "action", "target_kind",
            "target_id", "outcome", "detail_json",
        ])
        yield buf.getvalue()
        buf.seek(0); buf.truncate()
        for r in rows:
            writer.writerow([
                r.id,
                r.at.isoformat() if r.at else "",
                r.actor, r.actor_kind, r.action,
                r.target_kind or "", r.target_id or "",
                r.outcome,
                json.dumps(r.detail_json, separators=(",", ":")) if r.detail_json else "",
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate()

    fname = f"neuron-audit-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
