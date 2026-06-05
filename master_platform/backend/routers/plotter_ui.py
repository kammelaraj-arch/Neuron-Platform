"""SmartPlotter (APP-0002) device + scene registry + control.

This Master-side wiring lets Alexa Scene voice commands ("Alexa,
activate jalebi") drive a remote Pi running the SmartPlotter app. The
Pi already exposes the right API (auth-gated POST /api/profile/{id}/run)
— we just map Alexa-friendly scene names to a (device, profile_id) pair
and proxy the call.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, PlotterDevice, PlotterScene
from ..security.audit import record
from ..security.secret_crypto import decrypt_secret, encrypt_secret
from ..security.ui_auth import ui_require_admin

_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-plotter"])


# ── helpers exposed to Alexa directive handler ──────────────────────
async def activate_scene(session: AsyncSession, scene_id: str) -> dict:
    """Resolve scene → device, then run it via the pull-agent (queued
    for the Pi's next poll) if the device has an agent_token, or
    direct HTTP if the operator configured a reachable base_url.
    Used by both the UI button and the Alexa SceneController
    dispatcher so voice and click paths share one code path + audit
    trail."""
    scene = await session.get(PlotterScene, scene_id)
    if scene is None:
        raise HTTPException(404, f"scene {scene_id} not found")
    device = await session.get(PlotterDevice, scene.device_id)
    if device is None or device.status != "active":
        raise HTTPException(404, "plotter device not found or inactive")

    # Pull-agent path — Master never originates a connection to the Pi.
    if device.agent_token:
        from .agent import enqueue
        cmd = await enqueue(
            session,
            device_id=device.id,
            device_kind="plotter",
            kind="plotter.run",
            payload={"profile_id": scene.pi_profile_id},
        )
        return {"ok": True, "scene": scene.name, "device": device.label,
                "mode": "queued", "command_id": cmd.id}

    # Direct-HTTP path — only works when Master and device share a
    # network path. Kept for parity with the original v1 design and
    # for cases where Master + Pi are on the same LAN (no NAT).
    headers = {}
    if device.api_key_encrypted:
        headers["Authorization"] = f"Bearer {decrypt_secret(device.api_key_encrypted)}"
    url = f"{device.base_url.rstrip('/')}/api/profile/{scene.pi_profile_id}/run"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(502, f"plotter unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(502, f"plotter rejected run: HTTP {r.status_code} {r.text[:160]}")
    return {"ok": True, "scene": scene.name, "device": device.label,
            "mode": "direct", "pi_status": r.status_code}


# ── page ─────────────────────────────────────────────────────────────
@router.get("/ui/plotter", response_class=HTMLResponse)
async def ui_plotter(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    devices = (await session.execute(
        select(PlotterDevice).order_by(PlotterDevice.label)
    )).scalars().all()
    scenes_by_device: dict[str, list[PlotterScene]] = {}
    if devices:
        ids = [d.id for d in devices]
        scenes = (await session.execute(
            select(PlotterScene).where(PlotterScene.device_id.in_(ids))
            .order_by(PlotterScene.name)
        )).scalars().all()
        for s in scenes:
            scenes_by_device.setdefault(s.device_id, []).append(s)
    flash = request.session.pop("plotter_flash", None)
    one_time_agent_token = request.session.pop("plotter_agent_token", None)
    public_base = (request.headers.get("x-forwarded-proto", request.url.scheme)
                   + "://"
                   + (request.headers.get("x-forwarded-host")
                      or request.headers.get("host")
                      or request.url.netloc))
    return templates.TemplateResponse(
        "plotter.html",
        {
            "request": request,
            "signed_in": True,
            "devices": devices,
            "scenes_by_device": scenes_by_device,
            "flash": flash,
            "one_time_agent_token": one_time_agent_token,
            "public_base": public_base.rstrip("/"),
        },
    )


# ── device CRUD ──────────────────────────────────────────────────────
@router.post("/ui/plotter/devices/new")
async def ui_plotter_new_device(
    request: Request,
    label: str = Form(...),
    base_url: str = Form(...),
    api_key: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if not label.strip() or not base_url.strip():
        raise HTTPException(400, "label and base_url are required")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "base_url must start with http:// or https://")
    row = PlotterDevice(
        label=label.strip(),
        base_url=base_url.strip().rstrip("/"),
        api_key_encrypted=encrypt_secret(api_key) if api_key.strip() else None,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="plotter.device.create", target_kind="plotter_device",
        target_id=row.id, detail={"label": row.label, "base_url": row.base_url,
                                  "has_api_key": bool(row.api_key_encrypted)},
    )
    await session.commit()
    request.session["plotter_flash"] = {"kind": "emerald",
        "msg": f"Registered plotter '{row.label}' at {row.base_url}."}
    return RedirectResponse("/ui/plotter", status_code=303)


@router.post("/ui/plotter/devices/{device_id}/enable-agent")
async def ui_plotter_enable_agent(
    device_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    """Generate (or rotate) the bearer token the Pi-side neuron-agent
    uses to poll. The plaintext is stashed in the session so the next
    page render shows it ONCE for copy/paste into the Pi's env file."""
    import secrets
    device = await session.get(PlotterDevice, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    token = secrets.token_urlsafe(36)
    device.agent_token = token
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="plotter.device.agent_token_rotate",
        target_kind="plotter_device", target_id=device.id,
    )
    await session.commit()
    request.session["plotter_agent_token"] = {"device_id": device.id, "token": token}
    request.session["plotter_flash"] = {"kind": "emerald",
        "msg": "Agent token generated — paste into the Pi's /etc/default/neuron-agent now (shown once)."}
    return RedirectResponse("/ui/plotter", status_code=303)


@router.post("/ui/plotter/devices/{device_id}/delete")
async def ui_plotter_delete_device(
    device_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(PlotterDevice, device_id)
    if row is None:
        raise HTTPException(404, "device not found")
    # Cascade: remove scenes pointing at this device.
    scenes = (await session.execute(
        select(PlotterScene).where(PlotterScene.device_id == device_id)
    )).scalars().all()
    for s in scenes:
        await session.delete(s)
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="plotter.device.delete", target_kind="plotter_device",
        target_id=device_id, detail={"scenes_removed": len(scenes)},
    )
    await session.commit()
    request.session["plotter_flash"] = {"kind": "red",
        "msg": f"Deleted device + {len(scenes)} scene(s)."}
    return RedirectResponse("/ui/plotter", status_code=303)


@router.post("/ui/plotter/devices/{device_id}/ping", response_class=JSONResponse)
async def ui_plotter_ping(
    device_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    device = await session.get(PlotterDevice, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    headers = {}
    if device.api_key_encrypted:
        headers["Authorization"] = f"Bearer {decrypt_secret(device.api_key_encrypted)}"
    url = f"{device.base_url.rstrip('/')}/healthz"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url, headers=headers)
        ok = r.status_code == 200
    except httpx.RequestError as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": ok, "http_status": r.status_code,
            "body": r.text[:200] if not ok else "healthy"}


# ── scene CRUD ──────────────────────────────────────────────────────
@router.post("/ui/plotter/devices/{device_id}/scenes/new")
async def ui_plotter_new_scene(
    device_id: str,
    request: Request,
    name: str = Form(...),
    pi_profile_id: int = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    device = await session.get(PlotterDevice, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    if not name.strip():
        raise HTTPException(400, "name is required")
    row = PlotterScene(
        device_id=device_id,
        name=name.strip().lower(),
        pi_profile_id=int(pi_profile_id),
        description=description.strip() or None,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="plotter.scene.create", target_kind="plotter_scene",
        target_id=row.id, detail={"name": row.name, "pi_profile_id": row.pi_profile_id,
                                  "device_id": device_id},
    )
    await session.commit()
    request.session["plotter_flash"] = {"kind": "emerald",
        "msg": f"Added scene '{row.name}' → profile {row.pi_profile_id}."}
    return RedirectResponse("/ui/plotter", status_code=303)


@router.post("/ui/plotter/scenes/{scene_id}/delete")
async def ui_plotter_delete_scene(
    scene_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(PlotterScene, scene_id)
    if row is None:
        raise HTTPException(404, "scene not found")
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="plotter.scene.delete", target_kind="plotter_scene",
        target_id=scene_id,
    )
    await session.commit()
    request.session["plotter_flash"] = {"kind": "red", "msg": "Scene removed."}
    return RedirectResponse("/ui/plotter", status_code=303)


@router.post("/ui/plotter/scenes/{scene_id}/activate")
async def ui_plotter_activate(
    scene_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    result = await activate_scene(session, scene_id)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="plotter.scene.activate", target_kind="plotter_scene",
        target_id=scene_id, detail=result,
    )
    await session.commit()
    request.session["plotter_flash"] = {"kind": "emerald",
        "msg": f"Plotter started: {result['scene']} on {result['device']}."}
    return RedirectResponse("/ui/plotter", status_code=303)
