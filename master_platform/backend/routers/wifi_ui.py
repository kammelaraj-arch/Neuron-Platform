"""Admin UI for WiFi network profiles.

Pages:
  GET  /ui/wifi                  list profiles
  GET  /ui/wifi/new              create form
  POST /ui/wifi/new              persist (encrypts password)
  GET  /ui/wifi/{id}             detail + edit form
  POST /ui/wifi/{id}             update (re-encrypts password only if changed)
  POST /ui/wifi/{id}/delete      delete (refuses if any group references it)

Passwords are encrypted at rest by security.secret_crypto. Plaintext is
NEVER returned by GET endpoints; the edit form leaves the password
field blank and only updates it if a new value is provided.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, EdgeGroup, WIFI_SECURITY_TYPES, WifiNetwork
from ..security.audit import record
from ..security.secret_crypto import encrypt_secret
from ..security.ui_auth import ui_require_admin


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-wifi"])


@router.get("/ui/wifi", response_class=HTMLResponse)
async def wifi_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    rows = (
        await session.execute(
            select(WifiNetwork).order_by(WifiNetwork.name)
        )
    ).scalars().all()
    # Count groups referencing each profile so the UI can show usage.
    usage: dict[str, int] = {}
    if rows:
        groups = (
            await session.execute(
                select(EdgeGroup.primary_wifi_id, EdgeGroup.secondary_wifi_id)
            )
        ).all()
        for p, s in groups:
            if p:
                usage[p] = usage.get(p, 0) + 1
            if s:
                usage[s] = usage.get(s, 0) + 1
    return templates.TemplateResponse(
        "wifi.html",
        {"request": request, "rows": rows, "usage": usage,
         "SECURITY_TYPES": WIFI_SECURITY_TYPES},
    )


@router.get("/ui/wifi/new", response_class=HTMLResponse)
async def wifi_new_page(
    request: Request, actor: APIKey = Depends(ui_require_admin),
):
    return templates.TemplateResponse(
        "wifi_form.html",
        {"request": request, "row": None, "SECURITY_TYPES": WIFI_SECURITY_TYPES},
    )


@router.post("/ui/wifi/new")
async def wifi_create(
    request: Request,
    name: str = Form(...),
    ssid: str = Form(...),
    security: str = Form("wpa2"),
    username: str = Form(""),
    password: str = Form(""),
    hidden: str = Form(""),
    country_code: str = Form(""),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    name = name.strip()
    ssid = ssid.strip()
    if not name or not ssid:
        raise HTTPException(400, "name and ssid are required")
    if security not in WIFI_SECURITY_TYPES:
        raise HTTPException(400, f"invalid security type: {security}")
    if security != "open" and not password:
        raise HTTPException(400, "password is required for non-open networks")

    clash = await session.scalar(select(WifiNetwork).where(WifiNetwork.name == name))
    if clash is not None:
        raise HTTPException(409, f"WiFi profile '{name}' already exists")

    row = WifiNetwork(
        name=name,
        ssid=ssid,
        security=security,
        username=(username.strip() or None) if security == "wpa2_enterprise" else None,
        password_encrypted=encrypt_secret(password) if password else None,
        hidden=(hidden.lower() in ("on", "true", "1", "yes")),
        country_code=(country_code.strip().upper() or None),
        notes=notes.strip() or None,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="wifi.create", target_kind="wifi_network", target_id=row.id,
        detail={"name": name, "ssid": ssid, "security": security},
    )
    await session.commit()
    return RedirectResponse("/ui/wifi", status_code=303)


@router.get("/ui/wifi/{wifi_id}", response_class=HTMLResponse)
async def wifi_detail(
    request: Request, wifi_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(WifiNetwork, wifi_id)
    if row is None:
        raise HTTPException(404, "WiFi profile not found")
    # Count usage
    groups = (
        await session.execute(
            select(EdgeGroup).where(
                or_(EdgeGroup.primary_wifi_id == wifi_id,
                    EdgeGroup.secondary_wifi_id == wifi_id)
            )
        )
    ).scalars().all()
    return templates.TemplateResponse(
        "wifi_form.html",
        {"request": request, "row": row, "SECURITY_TYPES": WIFI_SECURITY_TYPES,
         "using_groups": groups},
    )


@router.post("/ui/wifi/{wifi_id}")
async def wifi_update(
    request: Request, wifi_id: str,
    name: str = Form(...),
    ssid: str = Form(...),
    security: str = Form("wpa2"),
    username: str = Form(""),
    password: str = Form(""),
    hidden: str = Form(""),
    country_code: str = Form(""),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(WifiNetwork, wifi_id)
    if row is None:
        raise HTTPException(404, "WiFi profile not found")
    if security not in WIFI_SECURITY_TYPES:
        raise HTTPException(400, f"invalid security type: {security}")
    row.name = name.strip() or row.name
    row.ssid = ssid.strip() or row.ssid
    row.security = security
    row.username = (username.strip() or None) if security == "wpa2_enterprise" else None
    if password:  # only re-encrypt if a new value is supplied
        row.password_encrypted = encrypt_secret(password)
    row.hidden = (hidden.lower() in ("on", "true", "1", "yes"))
    row.country_code = country_code.strip().upper() or None
    row.notes = notes.strip() or None
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="wifi.update", target_kind="wifi_network", target_id=row.id,
        detail={"password_rotated": bool(password)},
    )
    await session.commit()
    return RedirectResponse("/ui/wifi", status_code=303)


@router.post("/ui/wifi/{wifi_id}/delete")
async def wifi_delete(
    request: Request, wifi_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(WifiNetwork, wifi_id)
    if row is None:
        raise HTTPException(404, "WiFi profile not found")
    referencing = (
        await session.execute(
            select(EdgeGroup).where(
                or_(EdgeGroup.primary_wifi_id == wifi_id,
                    EdgeGroup.secondary_wifi_id == wifi_id)
            )
        )
    ).scalars().all()
    if referencing:
        raise HTTPException(409,
            f"refusing to delete — {len(referencing)} group(s) still reference this WiFi profile")
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="wifi.delete", target_kind="wifi_network", target_id=wifi_id,
    )
    await session.commit()
    return RedirectResponse("/ui/wifi", status_code=303)
