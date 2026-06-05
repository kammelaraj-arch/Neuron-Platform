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

from datetime import datetime

from ..db import get_session
from ..fabric import FabricDevice, get_device, list_devices
from ..models import APIKey, DeviceAsset, DeviceGroup, DeviceGroupMembership
from ..security.audit import record
from ..security.ui_auth import ui_require_admin


def _parse_date(s: str | None) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def _parse_float(s: str | None) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None

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


# ── asset register edit ─────────────────────────────────────────────
@router.get("/ui/fabric/{fabric_id_kind}/{fabric_id_pk}/asset", response_class=HTMLResponse)
async def ui_fabric_asset_edit(
    fabric_id_kind: str,
    fabric_id_pk: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    fabric_id = f"{fabric_id_kind}:{fabric_id_pk}"
    device = await get_device(session, fabric_id)
    if device is None:
        raise HTTPException(404, f"device {fabric_id} not found")
    asset = await session.get(DeviceAsset, fabric_id)
    flash = request.session.pop("fabric_flash", None)
    return templates.TemplateResponse(
        "fabric_asset.html",
        {
            "request": request, "signed_in": True,
            "device": device, "asset": asset,
            "flash": flash,
        },
    )


@router.post("/ui/fabric/{fabric_id_kind}/{fabric_id_pk}/asset")
async def ui_fabric_asset_save(
    fabric_id_kind: str,
    fabric_id_pk: str,
    request: Request,
    category: str = Form(""),
    sub_category: str = Form(""),
    location: str = Form(""),
    address: str = Form(""),
    gps_lat: str = Form(""),
    gps_lon: str = Form(""),
    install_date: str = Form(""),
    warranty_expires_at: str = Form(""),
    vendor: str = Form(""),
    purchase_ref: str = Form(""),
    purchase_price: str = Form(""),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    fabric_id = f"{fabric_id_kind}:{fabric_id_pk}"
    if await get_device(session, fabric_id) is None:
        raise HTTPException(404, f"device {fabric_id} not found")
    row = await session.get(DeviceAsset, fabric_id)
    if row is None:
        row = DeviceAsset(fabric_id=fabric_id)
        session.add(row)
    row.category = category.strip() or None
    row.sub_category = sub_category.strip() or None
    row.location = location.strip() or None
    row.address = address.strip() or None
    row.gps_lat = _parse_float(gps_lat)
    row.gps_lon = _parse_float(gps_lon)
    row.install_date = _parse_date(install_date)
    row.warranty_expires_at = _parse_date(warranty_expires_at)
    row.vendor = vendor.strip() or None
    row.purchase_ref = purchase_ref.strip() or None
    row.purchase_price = _parse_float(purchase_price)
    row.notes = notes.strip() or None
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="fabric.asset.save", target_kind="device_asset",
        target_id=fabric_id,
        detail={"location": row.location, "category": row.category,
                "warranty_expires_at": warranty_expires_at,
                "purchase_price": row.purchase_price},
    )
    await session.commit()
    request.session["fabric_flash"] = {"kind": "emerald",
        "msg": f"Asset details saved for {fabric_id}."}
    return RedirectResponse(f"/ui/fabric/{fabric_id_kind}/{fabric_id_pk}/asset",
                            status_code=303)


# ── capability-driven picker + bulk assign ─────────────────────────
@router.get("/ui/fabric/assign", response_class=HTMLResponse)
async def ui_fabric_assign(
    request: Request,
    capability: str = "",
    provider: str = "",
    group: str = "",
    status: str = "",
    q: str = "",
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    devices = await list_devices(
        session,
        provider=provider or None, group=group or None, q=q or None,
    )
    if capability:
        devices = [d for d in devices if capability in d.capabilities]
    if status:
        devices = [d for d in devices if d.status == status]
    # Build the capability facet from the *unfiltered* set so the
    # operator can switch capabilities without resetting other filters.
    all_devices = await list_devices(session)
    caps: dict[str, int] = {}
    for d in all_devices:
        for c in d.capabilities:
            caps[c] = caps.get(c, 0) + 1
    providers_facet = sorted({d.provider for d in all_devices if d.provider})
    groups = (await session.execute(
        select(DeviceGroup).order_by(DeviceGroup.sort_order, DeviceGroup.label)
    )).scalars().all()
    flash = request.session.pop("fabric_flash", None)
    return templates.TemplateResponse(
        "fabric_assign.html",
        {
            "request": request, "signed_in": True,
            "devices": devices, "groups": groups,
            "capability_facets": sorted(caps.items(), key=lambda x: (-x[1], x[0])),
            "provider_facets": providers_facet,
            "filters": {"capability": capability, "provider": provider,
                        "group": group, "status": status, "q": q},
            "flash": flash,
        },
    )


@router.post("/api/fabric/bulk-membership", response_class=JSONResponse)
async def api_fabric_bulk_membership(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    """Add/remove a batch of devices to/from a group. Optionally
    create the group inline if it doesn't exist yet (single-screen
    flow — pick capability → select devices → name new group → done)."""
    body = await request.json()
    fabric_ids = body.get("fabric_ids") or []
    group_name = (body.get("group_name") or "").strip().lower().replace(" ", "-")
    action = (body.get("action") or "add").lower()
    create_label = (body.get("create_label") or "").strip()
    if not fabric_ids or not group_name:
        raise HTTPException(400, "fabric_ids[] and group_name required")
    if action not in ("add", "remove"):
        raise HTTPException(400, "action must be 'add' or 'remove'")

    # Inline-create the group when caller flagged it.
    group = await session.get(DeviceGroup, group_name)
    if group is None:
        if not create_label:
            raise HTTPException(404, f"group '{group_name}' not found "
                                "(pass create_label to make it inline)")
        group = DeviceGroup(
            name=group_name, label=create_label,
            color=body.get("color") or "#10b981",
        )
        session.add(group)
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="fabric.group.create_inline", target_kind="device_group",
            target_id=group_name, detail={"label": create_label},
        )

    added = removed = skipped = 0
    for fid in fabric_ids:
        # Verify each fabric_id resolves so we don't accept opaque junk.
        if await get_device(session, fid) is None:
            skipped += 1
            continue
        existing = await session.get(
            DeviceGroupMembership, {"fabric_id": fid, "group_name": group_name}
        )
        if action == "add":
            if existing is None:
                session.add(DeviceGroupMembership(
                    fabric_id=fid, group_name=group_name))
                added += 1
            else:
                skipped += 1
        else:
            if existing is not None:
                await session.delete(existing)
                removed += 1
            else:
                skipped += 1
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=f"fabric.bulk_membership.{action}",
        target_kind="device_group", target_id=group_name,
        detail={"added": added, "removed": removed, "skipped": skipped,
                "total": len(fabric_ids)},
    )
    await session.commit()
    return {"ok": True, "group": group_name,
            "added": added, "removed": removed, "skipped": skipped}


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
