"""Admin UI for the feature/capability request tracker.

See CLAUDE.md for the durable rules. Pages:
  GET  /ui/features                 → list view with filter chips
  GET  /ui/features/new             → create form
  POST /ui/features/new             → persist
  GET  /ui/features/{short_id}      → detail + status-transition controls
  POST /ui/features/{short_id}      → update (status, notes, deploy SHAs)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import (
    FEATURE_PRIORITIES,
    FEATURE_STATUSES,
    APIKey,
    AuditEvent,
    FeatureRequest,
)
from ..security.audit import record
from ..security.ui_auth import ui_require_admin


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-features"])


async def _next_short_id(session: AsyncSession) -> str:
    """Generate the next FR-NNNN short id by incrementing the max."""
    # Fetch the current max numeric suffix. short_ids look like "FR-0042".
    rows = (await session.execute(select(FeatureRequest.short_id))).scalars().all()
    max_n = 0
    for s in rows:
        if s and s.startswith("FR-"):
            try:
                max_n = max(max_n, int(s.split("-", 1)[1]))
            except (ValueError, IndexError):
                continue
    return f"FR-{max_n + 1:04d}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/ui/features", response_class=HTMLResponse)
async def features_page(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    q: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    stmt = select(FeatureRequest)
    if status and status in FEATURE_STATUSES:
        stmt = stmt.where(FeatureRequest.status == status)
    if priority and priority in FEATURE_PRIORITIES:
        stmt = stmt.where(FeatureRequest.priority == priority)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (FeatureRequest.title.ilike(like))
            | (FeatureRequest.description.ilike(like))
            | (FeatureRequest.short_id.ilike(like))
        )
    stmt = stmt.order_by(desc(FeatureRequest.created_at))
    rows = (await session.execute(stmt)).scalars().all()

    # Status counts for the filter chips.
    counts_stmt = select(FeatureRequest.status, func.count()).group_by(FeatureRequest.status)
    raw = (await session.execute(counts_stmt)).all()
    counts = {s: 0 for s in FEATURE_STATUSES}
    counts.update({s: n for s, n in raw})

    return templates.TemplateResponse(
        "features.html",
        {
            "request": request,
            "rows": rows,
            "counts": counts,
            "filter_status": status,
            "filter_priority": priority,
            "filter_q": q or "",
            "STATUSES": FEATURE_STATUSES,
            "PRIORITIES": FEATURE_PRIORITIES,
        },
    )


@router.get("/ui/features/new", response_class=HTMLResponse)
async def feature_new_page(
    request: Request,
    actor: APIKey = Depends(ui_require_admin),
):
    return templates.TemplateResponse(
        "feature_form.html",
        {
            "request": request,
            "row": None,
            "STATUSES": FEATURE_STATUSES,
            "PRIORITIES": FEATURE_PRIORITIES,
            "default_requested_by": actor.label or actor.owner or "admin",
        },
    )


@router.post("/ui/features/new")
async def feature_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    requested_by: str = Form(...),
    priority: str = Form("normal"),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    if priority not in FEATURE_PRIORITIES:
        raise HTTPException(400, f"invalid priority: {priority}")

    short_id = await _next_short_id(session)
    fr = FeatureRequest(
        short_id=short_id,
        title=title,
        description=(description or "").strip() or None,
        requested_by=(requested_by or "").strip() or "anonymous",
        priority=priority,
        status="requested",
    )
    session.add(fr)
    await session.flush()
    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action="feature.create",
        target_kind="feature_request",
        target_id=fr.short_id,
        detail={"title": title, "priority": priority},
    )
    await session.commit()
    return RedirectResponse(f"/ui/features/{short_id}", status_code=303)


@router.get("/ui/features/{short_id}", response_class=HTMLResponse)
async def feature_detail_page(
    request: Request,
    short_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    fr = (
        await session.execute(
            select(FeatureRequest).where(FeatureRequest.short_id == short_id)
        )
    ).scalar_one_or_none()
    if fr is None:
        raise HTTPException(404, "feature request not found")
    return templates.TemplateResponse(
        "feature_detail.html",
        {
            "request": request,
            "row": fr,
            "STATUSES": FEATURE_STATUSES,
            "PRIORITIES": FEATURE_PRIORITIES,
        },
    )


@router.post("/ui/features/{short_id}")
async def feature_update(
    request: Request,
    short_id: str,
    status: str = Form(...),
    priority: str = Form("normal"),
    title: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
    git_sha_dev: str = Form(""),
    git_sha_prod: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    fr = (
        await session.execute(
            select(FeatureRequest).where(FeatureRequest.short_id == short_id)
        )
    ).scalar_one_or_none()
    if fr is None:
        raise HTTPException(404, "feature request not found")
    if status not in FEATURE_STATUSES:
        raise HTTPException(400, f"invalid status: {status}")
    if priority not in FEATURE_PRIORITIES:
        raise HTTPException(400, f"invalid priority: {priority}")

    prev_status = fr.status
    fr.title = title.strip() or fr.title
    fr.description = (description or "").strip() or None
    fr.notes = (notes or "").strip() or None
    fr.priority = priority
    fr.status = status
    fr.git_sha_dev = (git_sha_dev or "").strip() or fr.git_sha_dev
    fr.git_sha_prod = (git_sha_prod or "").strip() or fr.git_sha_prod

    # Auto-stamp deploy timestamps on status transitions if not already set.
    if status == "deployed_dev" and fr.deployed_dev_at is None:
        fr.deployed_dev_at = _now()
    if status == "deployed_prod" and fr.deployed_prod_at is None:
        fr.deployed_prod_at = _now()

    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action="feature.update",
        target_kind="feature_request",
        target_id=fr.short_id,
        detail={"from": prev_status, "to": status, "priority": priority},
    )
    await session.commit()
    return RedirectResponse(f"/ui/features/{short_id}", status_code=303)
