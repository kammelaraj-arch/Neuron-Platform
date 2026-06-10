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
from ..models import (
    APIKey, DeviceAsset, DeviceGrant, DeviceGroup, DeviceGroupMembership,
    DeviceOrgAssignment, DeviceSafety, FAILSAFE_ACTIONS, OrgMembership,
    Organization, RISK_LEVELS, RISK_TYPES, User,
)
from ..security.audit import record
from ..security.ui_auth import (
    SESSION_CURRENT_ORG_KEY, SESSION_USER_KEY, ui_require_admin,
)


async def _actor_org_names(session: AsyncSession, request: Request,
                            actor: APIKey) -> list[str] | None:
    """Return the list of org names this caller can see for this
    request — ONE org when an org-switcher is active, or the full
    membership list when no switcher value is set (first request after
    login, before they touch the picker).

    None for super-admin / non-user callers (no multi-tenant filter).

    Self-healing: if the session points at an org the user no longer
    belongs to (admin revoked the membership in the meantime), the
    saved value is dropped and we fall through to their first
    remaining membership.
    """
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    memberships = (await session.execute(
        select(OrgMembership).where(OrgMembership.user_id == user_id)
    )).scalars().all()
    if not memberships:
        return ["personal"]
    member_names = {m.org_name for m in memberships}
    current = request.session.get(SESSION_CURRENT_ORG_KEY)
    if current and current in member_names:
        return [current]
    # No valid current → pick the first deterministically (alpha) and
    # save it back to the session so subsequent requests are stable.
    chosen = sorted(member_names)[0]
    request.session[SESSION_CURRENT_ORG_KEY] = chosen
    return [chosen]


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
    import traceback as _tb
    error_panel: str | None = None
    devices = []
    groups = []
    facet_providers: dict[str, int] = {}
    facet_kinds: dict[str, int] = {}
    facet_categories: dict[str, int] = {}
    try:
        org_names = await _actor_org_names(session, request, actor)
        devices = await list_devices(
            session,
            kind=kind or None, provider=provider or None,
            category=category or None, group=group or None, q=q or None,
            org_names=org_names,
        )
        groups = (await session.execute(
            select(DeviceGroup).order_by(DeviceGroup.sort_order, DeviceGroup.label)
        )).scalars().all()
        # Aggregate facets so the sidebar can show counts.
        for d in devices:
            if d.provider:
                facet_providers[d.provider] = facet_providers.get(d.provider, 0) + 1
            facet_kinds[d.kind] = facet_kinds.get(d.kind, 0) + 1
            facet_categories[d.category] = facet_categories.get(d.category, 0) + 1
    except Exception as e:
        # Defensive: surface the actual exception inline so the operator
        # can see what's wrong without needing /api/admin/last-error.
        error_panel = f"{type(e).__name__}: {e}\n\n{_tb.format_exc()}"
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
            "error_panel": error_panel,
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
    org_names = await _actor_org_names(session, request, actor)
    if org_names is not None:
        owner = device.org_name or "personal"
        if owner not in org_names:
            raise HTTPException(404, f"device {fabric_id} not found")
    asset = await session.get(DeviceAsset, fabric_id)
    safety = await session.get(DeviceSafety, fabric_id)
    orgs = (await session.execute(select(Organization).order_by(Organization.label))).scalars().all()
    flash = request.session.pop("fabric_flash", None)
    return templates.TemplateResponse(
        "fabric_asset.html",
        {
            "request": request, "signed_in": True,
            "device": device, "asset": asset, "safety": safety,
            "orgs": orgs,
            "risk_levels": RISK_LEVELS, "risk_types": RISK_TYPES,
            "failsafe_actions": FAILSAFE_ACTIONS,
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
    # Safety fields.
    risk_level: str = Form("nominal"),
    risk_types: list[str] | None = Form(None),
    failsafe_action: str = Form("alarm_only"),
    failsafe_value: str = Form(""),
    disconnect_grace_seconds: int = Form(30),
    watchdog_ms: int = Form(1000),
    hazard_notes: str = Form(""),
    # Org assignment.
    org_name: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    fabric_id = f"{fabric_id_kind}:{fabric_id_pk}"
    device = await get_device(session, fabric_id)
    if device is None:
        raise HTTPException(404, f"device {fabric_id} not found")
    org_names = await _actor_org_names(session, request, actor)
    if org_names is not None:
        owner = device.org_name or "personal"
        if owner not in org_names:
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

    # ── Safety / risk ────────────────────────────────────────────
    safety = await session.get(DeviceSafety, fabric_id)
    if safety is None:
        safety = DeviceSafety(fabric_id=fabric_id)
        session.add(safety)
    if risk_level and risk_level in RISK_LEVELS:
        safety.risk_level = risk_level
    safety.risk_types_json = [t for t in (risk_types or []) if t in RISK_TYPES]
    if failsafe_action and failsafe_action in FAILSAFE_ACTIONS:
        safety.failsafe_action = failsafe_action
    if failsafe_value.strip():
        import json as _json
        try:
            safety.failsafe_value_json = _json.loads(failsafe_value)
        except Exception:
            safety.failsafe_value_json = {"raw": failsafe_value.strip()}
    else:
        safety.failsafe_value_json = None
    safety.disconnect_grace_seconds = max(1, min(86400, int(disconnect_grace_seconds)))
    safety.watchdog_ms = max(50, min(600000, int(watchdog_ms)))
    safety.hazard_notes = hazard_notes.strip() or None
    safety.last_reviewed_at = datetime.now()
    safety.last_reviewed_by = actor.id

    # ── Org assignment ───────────────────────────────────────────
    if org_name.strip():
        if await session.get(Organization, org_name.strip()) is None:
            raise HTTPException(400, f"unknown org '{org_name}'")
        # Caller can only re-assign devices to orgs they belong to —
        # otherwise an operator could lift a device into a tenant
        # they have no read access to and lose it.
        if org_names is not None and org_name.strip() not in org_names:
            raise HTTPException(403,
                f"you are not a member of org '{org_name}'")
        existing = await session.get(DeviceOrgAssignment, fabric_id)
        if existing is None:
            session.add(DeviceOrgAssignment(
                fabric_id=fabric_id, org_name=org_name.strip(),
                assigned_by=actor.id,
            ))
        else:
            existing.org_name = org_name.strip()
            existing.assigned_by = actor.id

    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="fabric.asset.save", target_kind="device_asset",
        target_id=fabric_id,
        detail={
            "location": row.location, "category": row.category,
            "warranty_expires_at": warranty_expires_at,
            "purchase_price": row.purchase_price,
            "risk_level": safety.risk_level,
            "failsafe_action": safety.failsafe_action,
            "org_name": org_name.strip() or None,
        },
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
    org_names = await _actor_org_names(session, request, actor)
    devices = await list_devices(
        session,
        provider=provider or None, group=group or None, q=q or None,
        org_names=org_names,
    )
    if capability:
        devices = [d for d in devices if capability in d.capabilities]
    if status:
        devices = [d for d in devices if d.status == status]
    # Build the capability facet from the *unfiltered* set so the
    # operator can switch capabilities without resetting other filters.
    # Same org scope so facet counts match what the caller can see.
    all_devices = await list_devices(session, org_names=org_names)
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

    org_names = await _actor_org_names(session, request, actor)
    added = removed = skipped = 0
    for fid in fabric_ids:
        # Verify each fabric_id resolves AND belongs to an org the
        # caller can see — silently skip cross-tenant attempts.
        dev = await get_device(session, fid)
        if dev is None:
            skipped += 1
            continue
        if org_names is not None:
            owner = dev.org_name or "personal"
            if owner not in org_names:
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
    request: Request,
    kind: str = "",
    provider: str = "",
    category: str = "",
    group: str = "",
    q: str = "",
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    org_names = await _actor_org_names(session, request, actor)
    devices = await list_devices(
        session,
        kind=kind or None, provider=provider or None,
        category=category or None, group=group or None, q=q or None,
        org_names=org_names,
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
    request: Request,
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
    dev = await get_device(session, fabric_id)
    if dev is None:
        raise HTTPException(404, f"device '{fabric_id}' not found")
    # Cross-tenant guard — return 404 (not 403) so an attacker can't
    # enumerate devices in foreign orgs by varying fabric_id.
    org_names = await _actor_org_names(session, request, actor)
    if org_names is not None:
        owner = dev.org_name or "personal"
        if owner not in org_names:
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
