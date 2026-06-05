"""Organizations admin — multi-tenant boundary + capability grants.

Default policy: every org member browses the fabric read-only.
Control (write/execute on a device capability) requires an explicit
DeviceGrant for that (principal, target, capability) tuple. This
router exposes the admin surface to create orgs, add members, and
mint grants. Enforcement on integration control endpoints (Tado /
Ring / Plotter) lands in a follow-up commit so this one stays
additive — no behavioural change to existing routes yet.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import (
    APIKey, DeviceGrant, DeviceGroup, OrgMembership, Organization, User,
)
from ..security.audit import record
from ..security.ui_auth import ui_require_admin

_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["orgs"])


# ── list / create orgs ──────────────────────────────────────────────
@router.get("/ui/orgs", response_class=HTMLResponse)
async def ui_orgs(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    orgs = (await session.execute(
        select(Organization).order_by(Organization.label)
    )).scalars().all()
    # Member counts per org so the operator gets a feel for size.
    member_counts: dict[str, int] = {}
    if orgs:
        for o in orgs:
            count = await session.scalar(
                select(OrgMembership).where(OrgMembership.org_name == o.name)
                .with_only_columns(OrgMembership.user_id)
            )
            # Simpler: count via .scalars().all()
            rows = (await session.execute(
                select(OrgMembership).where(OrgMembership.org_name == o.name)
            )).scalars().all()
            member_counts[o.name] = len(rows)
    flash = request.session.pop("orgs_flash", None)
    return templates.TemplateResponse(
        "orgs.html",
        {
            "request": request, "signed_in": True,
            "orgs": orgs, "member_counts": member_counts,
            "flash": flash,
        },
    )


@router.post("/ui/orgs/new")
async def ui_orgs_new(
    request: Request,
    name: str = Form(...),
    label: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    name_clean = name.strip().lower().replace(" ", "-")
    if not name_clean or not label.strip():
        raise HTTPException(400, "name + label required")
    if await session.get(Organization, name_clean) is not None:
        raise HTTPException(409, f"org '{name_clean}' already exists")
    row = Organization(
        name=name_clean, label=label.strip(),
        description=description.strip() or None,
    )
    session.add(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="org.create", target_kind="organization", target_id=row.name,
        detail={"label": row.label},
    )
    await session.commit()
    request.session["orgs_flash"] = {"kind": "emerald",
        "msg": f"Organisation '{row.label}' created."}
    return RedirectResponse(f"/ui/orgs/{row.name}", status_code=303)


# ── per-org detail (members + grants) ───────────────────────────────
@router.get("/ui/orgs/{name}", response_class=HTMLResponse)
async def ui_org_detail(
    name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    org = await session.get(Organization, name)
    if org is None:
        raise HTTPException(404, "org not found")
    members = (await session.execute(
        select(OrgMembership).where(OrgMembership.org_name == name)
    )).scalars().all()
    users_by_id = {u.id: u for u in (await session.execute(
        select(User))).scalars().all()}
    grants = (await session.execute(
        select(DeviceGrant).where(DeviceGrant.org_name == name)
        .order_by(DeviceGrant.granted_at.desc())
    )).scalars().all()
    groups = (await session.execute(
        select(DeviceGroup).order_by(DeviceGroup.label)
    )).scalars().all()
    all_users = sorted(users_by_id.values(), key=lambda u: u.username)
    flash = request.session.pop("orgs_flash", None)
    return templates.TemplateResponse(
        "org_detail.html",
        {
            "request": request, "signed_in": True,
            "org": org, "members": members, "users_by_id": users_by_id,
            "all_users": all_users, "grants": grants, "groups": groups,
            "flash": flash,
        },
    )


# ── members ─────────────────────────────────────────────────────────
@router.post("/ui/orgs/{name}/members/add")
async def ui_org_add_member(
    name: str,
    request: Request,
    user_id: str = Form(...),
    role: str = Form("member"),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if role not in ("admin", "member"):
        raise HTTPException(400, "role must be admin or member")
    if await session.get(Organization, name) is None:
        raise HTTPException(404, "org not found")
    if await session.get(User, user_id) is None:
        raise HTTPException(404, "user not found")
    existing = await session.get(OrgMembership, {"org_name": name, "user_id": user_id})
    if existing is None:
        session.add(OrgMembership(org_name=name, user_id=user_id, role=role))
        verb = "added"
    else:
        existing.role = role
        verb = "role updated"
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="org.member.upsert", target_kind="org_membership",
        target_id=f"{name}/{user_id}",
        detail={"role": role},
    )
    await session.commit()
    request.session["orgs_flash"] = {"kind": "emerald",
        "msg": f"Member {verb}."}
    return RedirectResponse(f"/ui/orgs/{name}", status_code=303)


@router.post("/ui/orgs/{name}/members/{user_id}/remove")
async def ui_org_remove_member(
    name: str,
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(OrgMembership, {"org_name": name, "user_id": user_id})
    if row is None:
        raise HTTPException(404, "membership not found")
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="org.member.remove", target_kind="org_membership",
        target_id=f"{name}/{user_id}",
    )
    await session.commit()
    request.session["orgs_flash"] = {"kind": "red", "msg": "Member removed."}
    return RedirectResponse(f"/ui/orgs/{name}", status_code=303)


# ── grants ──────────────────────────────────────────────────────────
@router.post("/ui/orgs/{name}/grants/new")
async def ui_org_new_grant(
    name: str,
    request: Request,
    principal_kind: str = Form(...),     # user | app
    principal_id: str = Form(...),
    target_kind: str = Form(...),        # group | device
    target_id: str = Form(...),
    capabilities: str = Form(""),        # comma-separated
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if principal_kind not in ("user", "app") or target_kind not in ("group", "device"):
        raise HTTPException(400, "invalid principal_kind or target_kind")
    if await session.get(Organization, name) is None:
        raise HTTPException(404, "org not found")
    caps = [c.strip() for c in capabilities.split(",") if c.strip()]
    if not caps:
        raise HTTPException(400, "at least one capability is required")
    row = DeviceGrant(
        org_name=name, principal_kind=principal_kind,
        principal_id=principal_id.strip(),
        target_kind=target_kind, target_id=target_id.strip(),
        capabilities_json=caps, granted_by=actor.id,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="org.grant.create", target_kind="device_grant", target_id=row.id,
        detail={"org": name, "principal": f"{principal_kind}/{principal_id}",
                "target": f"{target_kind}/{target_id}", "caps": caps},
    )
    await session.commit()
    request.session["orgs_flash"] = {"kind": "emerald",
        "msg": f"Granted {', '.join(caps)} to {principal_kind} {principal_id}."}
    return RedirectResponse(f"/ui/orgs/{name}", status_code=303)


@router.post("/ui/orgs/{name}/grants/{grant_id}/revoke")
async def ui_org_revoke_grant(
    name: str,
    grant_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(DeviceGrant, grant_id)
    if row is None or row.org_name != name:
        raise HTTPException(404, "grant not found")
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="org.grant.revoke", target_kind="device_grant", target_id=grant_id,
    )
    await session.commit()
    request.session["orgs_flash"] = {"kind": "red", "msg": "Grant revoked."}
    return RedirectResponse(f"/ui/orgs/{name}", status_code=303)
