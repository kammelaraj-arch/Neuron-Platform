"""Fabric UI + JSON endpoints — unified device browser, groups, picker.

This is the operator-facing surface for the fabric layer:

  /ui/fabric                       — browser with provider / kind /
                                     group filters + group manager
  GET  /api/fabric/devices         — JSON list (used by the page + by
                                     reusable picker in other UIs)
  POST /api/fabric/groups          — create / update group metadata
  POST .../delete                  — remove a group
  POST /api/fabric/membership      — add / remove device from group

Auth: admin-only for groups + memberships (state-changing); the
GET list mirrors /ui's ui_require_admin so we don't expose device
topology to anon traffic.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..fabric import FabricDevice, get_device, list_devices
from ..models import APIKey, DeviceGroup, DeviceGroupMembership
from ..security.audit import record
from ..security.ui_auth import ui_require_admin

_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["fabric-ui"])


# ── page ────────────────────────────────────────────────────────────
@router.get("/ui/fabric", response_class=HTMLResponse)
async def ui_fabric(
    request: Request,
    kind: str = "",
    provider: str = "",
    category: str = "",
    group: str = "",
    q: str = "",
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    devices = await list_devices(
        session,
        kind=kind or None, provider=provider or None,
        category=category or None, group=group or None, q=q or None,
    )
    groups = (await session.execute(
        select(DeviceGroup).order_by(DeviceGroup.sort_order, DeviceGroup.label)
    )).scalars().all()
    # Aggregate facets so the sidebar can show counts.
    facet_providers: dict[str, int] = {}
    facet_kinds: dict[str, int] = {}
    facet_categories: dict[str, int] = {}
    for d in devices:
        if d.provider:
            facet_providers[d.provider] = facet_providers.get(d.provider, 0) + 1
        facet_kinds[d.kind] = facet_kinds.get(d.kind, 0) + 1
        facet_categories[d.category] = facet_categories.get(d.category, 0) + 1
    flash = request.session.pop("fabric_flash", None)
    return templates.TemplateResponse(
        "fabric.html",
        {
            "request": request, "signed_in": True,
            "devices": devices, "groups": groups,
            "facet_providers": sorted(facet_providers.items()),
            "facet_kinds": sorted(facet_kinds.items()),
            "facet_categories": sorted(facet_categories.items()),
            "filters": {"kind": kind, "provider": provider,
                        "category": category, "group": group, "q": q},
            "flash": flash,
        },
    )


# ── JSON device list (for picker + future composition surfaces) ────
@router.get("/api/fabric/devices", response_class=JSONResponse)
async def api_fabric_devices(
    kind: str = "",
    provider: str = "",
    category: str = "",
    group: str = "",
    q: str = "",
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    devices = await list_devices(
        session,
        kind=kind or None, provider=provider or None,
        category=category or None, group=group or None, q=q or None,
    )
    return [d.to_json() for d in devices]


# ── group CRUD ──────────────────────────────────────────────────────
@router.post("/ui/fabric/groups/new")
async def ui_fabric_new_group(
    request: Request,
    name: str = Form(...),
    label: str = Form(...),
    color: str = Form("#10b981"),
    room: str = Form(""),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    name_clean = name.strip().lower().replace(" ", "-")
    if not name_clean or not label.strip():
        raise HTTPException(400, "name and label required")
    exists = await session.get(DeviceGroup, name_clean)
    if exists is not None:
        raise HTTPException(409, f"group '{name_clean}' already exists")
    row = DeviceGroup(
        name=name_clean, label=label.strip(), color=color or "#10b981",
        room=room.strip() or None, description=description.strip() or None,
    )
    session.add(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="fabric.group.create", target_kind="device_group",
        target_id=row.name, detail={"label": row.label},
    )
    await session.commit()
    request.session["fabric_flash"] = {"kind": "emerald",
        "msg": f"Group '{row.label}' created."}
    return RedirectResponse("/ui/fabric", status_code=303)


@router.post("/ui/fabric/groups/{name}/delete")
async def ui_fabric_delete_group(
    name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(DeviceGroup, name)
    if row is None:
        raise HTTPException(404, "group not found")
    # Memberships are cascade-deleted by the FK ondelete.
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="fabric.group.delete", target_kind="device_group", target_id=name,
    )
    await session.commit()
    request.session["fabric_flash"] = {"kind": "red", "msg": f"Group '{name}' deleted."}
    return RedirectResponse("/ui/fabric", status_code=303)


# ── membership toggle ──────────────────────────────────────────────
@router.post("/api/fabric/membership", response_class=JSONResponse)
async def api_fabric_toggle_membership(
    fabric_id: str = Form(...),
    group_name: str = Form(...),
    action: str = Form("add"),    # "add" | "remove"
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    # Validate referenced group and device exist (the device check is
    # there to keep orphan memberships out — fabric_id is unverified
    # opaque from the form otherwise).
    if await session.get(DeviceGroup, group_name) is None:
        raise HTTPException(404, f"group '{group_name}' not found")
    if await get_device(session, fabric_id) is None:
        raise HTTPException(404, f"device '{fabric_id}' not found")
    existing = await session.get(
        DeviceGroupMembership, {"fabric_id": fabric_id, "group_name": group_name}
    )
    if action == "remove":
        if existing is not None:
            await session.delete(existing)
        action_logged = "fabric.membership.remove"
    else:
        if existing is None:
            session.add(DeviceGroupMembership(
                fabric_id=fabric_id, group_name=group_name,
            ))
        action_logged = "fabric.membership.add"
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=action_logged, target_kind="device_group_membership",
        target_id=f"{group_name}/{fabric_id}",
    )
    await session.commit()
    return {"ok": True, "fabric_id": fabric_id, "group_name": group_name,
            "action": "added" if action != "remove" else "removed"}
