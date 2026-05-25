"""Tado control UI + endpoints.

Sits on top of the VendorAccount framework (provider="tado"). Handles:

  - One-time device-code login per account (Tado killed password auth
    in 2025), storing the rotated refresh token encrypted.
  - Per-zone control: set temperature, off, resume schedule.
  - Whole-home presence: Home / Away / Auto.
  - Bulk control across EVERY tado account on the platform ("all
    accounts") — set-all-temp, all-off, all-resume.

Zones are surfaced as VendorDevice rows by the discovery driver, keyed
vendor_device_id="<home_id>:<zone_id>", so control endpoints resolve a
zone straight from the device row's metadata without a second lookup.

CLOUD integration — Master level only. Tado zones are advisory comfort
control, never a safety interlock (the local-brain failsafe contract
does not and cannot extend to a cloud thermostat).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, VendorAccount, VendorDevice
from ..security.audit import record
from ..security.secret_crypto import decrypt_secret, encrypt_secret
from ..security.ui_auth import ui_require_admin

_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-tado"])


# ── helpers ──────────────────────────────────────────────────────────
def _client_for(account: VendorAccount, rotated_holder: list):
    """Build a TadoClient for an account. Rotated refresh tokens land in
    ``rotated_holder`` so the caller can persist them after the call."""
    from ..integrations.tado import TadoClient
    extra = dict(account.extra_json or {})
    refresh = (
        decrypt_secret(account.refresh_token_encrypted)
        if account.refresh_token_encrypted else None
    )
    return TadoClient(
        refresh_token=refresh,
        client_id=extra.get("client_id"),
        api_base=account.base_url or None,
        on_token_refresh=lambda tok: rotated_holder.append(tok),
    )


def _persist_rotation(account: VendorAccount, rotated_holder: list) -> None:
    if rotated_holder:
        account.refresh_token_encrypted = encrypt_secret(rotated_holder[-1])


def _zone_addr(device: VendorDevice) -> tuple:
    md = device.metadata_json or {}
    home_id = md.get("home_id")
    zone_id = md.get("zone_id")
    if home_id is None or zone_id is None:
        # vendor_device_id is "<home>:<zone>" — fall back to splitting it.
        parts = (device.vendor_device_id or "").split(":", 1)
        if len(parts) == 2:
            home_id, zone_id = parts[0], parts[1]
    if home_id is None or zone_id is None:
        raise HTTPException(400, f"device {device.id} missing home/zone address")
    return home_id, zone_id


async def _tado_accounts(session: AsyncSession) -> list[VendorAccount]:
    return (await session.execute(
        select(VendorAccount)
        .where(VendorAccount.provider == "tado")
        .order_by(VendorAccount.label)
    )).scalars().all()


# ── page ─────────────────────────────────────────────────────────────
@router.get("/ui/tado", response_class=HTMLResponse)
async def ui_tado(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    accounts = await _tado_accounts(session)
    zones_by_account: dict[str, list[VendorDevice]] = {}
    if accounts:
        ids = [a.id for a in accounts]
        all_zones = (await session.execute(
            select(VendorDevice)
            .where(VendorDevice.vendor_account_id.in_(ids))
            .order_by(VendorDevice.name)
        )).scalars().all()
        for z in all_zones:
            zones_by_account.setdefault(z.vendor_account_id, []).append(z)
    flash = request.session.pop("tado_flash", None)
    return templates.TemplateResponse(
        "tado.html",
        {
            "request": request,
            "accounts": accounts,
            "zones_by_account": zones_by_account,
            "connected": {a.id: bool(a.refresh_token_encrypted) for a in accounts},
            "flash": flash,
            "signed_in": True,
        },
    )


# ── device-code login ────────────────────────────────────────────────
@router.post("/ui/tado/{account_id}/connect/start", response_class=JSONResponse)
async def ui_tado_connect_start(
    account_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    account = await session.get(VendorAccount, account_id)
    if account is None or account.provider != "tado":
        raise HTTPException(404, "tado account not found")
    client = _client_for(account, [])
    try:
        info = await client.start_device_login()
    except Exception as e:
        raise HTTPException(502, f"Tado device-login start failed: {e}")
    # Stash the device_code in the session for the matching poll call.
    request.session[f"tado_devicecode_{account_id}"] = info.get("device_code")
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="tado.connect.start", target_kind="vendor_account", target_id=account_id,
    )
    await session.commit()
    return {
        "verification_uri_complete": info.get("verification_uri_complete"),
        "verification_uri": info.get("verification_uri"),
        "user_code": info.get("user_code"),
        "interval": info.get("interval", 5),
        "expires_in": info.get("expires_in", 300),
    }


@router.post("/ui/tado/{account_id}/connect/poll", response_class=JSONResponse)
async def ui_tado_connect_poll(
    account_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    account = await session.get(VendorAccount, account_id)
    if account is None or account.provider != "tado":
        raise HTTPException(404, "tado account not found")
    device_code = request.session.get(f"tado_devicecode_{account_id}")
    if not device_code:
        raise HTTPException(400, "no device-login in progress — start first")
    client = _client_for(account, [])
    try:
        result = await client.poll_device_login(device_code)
    except Exception as e:
        request.session.pop(f"tado_devicecode_{account_id}", None)
        raise HTTPException(502, f"Tado device-login failed: {e}")
    if result.get("status") == "pending":
        return {"status": "pending"}
    # Approved — persist the refresh token, mark account active.
    account.refresh_token_encrypted = encrypt_secret(result["refresh_token"])
    account.status = "active"
    request.session.pop(f"tado_devicecode_{account_id}", None)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="tado.connect.complete", target_kind="vendor_account", target_id=account_id,
        detail={"has_refresh_token": True},
    )
    await session.commit()
    request.session["tado_flash"] = {"kind": "emerald",
                                     "msg": f"Tado connected: {account.label}. Now click Discover."}
    return {"status": "ok"}


# ── per-zone control ─────────────────────────────────────────────────
async def _zone_op(session, actor, device_id, op, **kw):
    device = await session.get(VendorDevice, device_id)
    if device is None:
        raise HTTPException(404, "zone not found")
    account = await session.get(VendorAccount, device.vendor_account_id)
    if account is None or account.provider != "tado":
        raise HTTPException(404, "tado account not found")
    home_id, zone_id = _zone_addr(device)
    rotated: list = []
    client = _client_for(account, rotated)
    try:
        if op == "temp":
            await client.set_zone_temperature(home_id, zone_id, kw["celsius"])
        elif op == "off":
            await client.set_zone_off(home_id, zone_id)
        elif op == "resume":
            await client.resume_schedule(home_id, zone_id)
        else:
            raise HTTPException(400, f"unknown op {op}")
    except Exception as e:
        raise HTTPException(502, f"Tado control failed: {e}")
    _persist_rotation(account, rotated)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=f"tado.zone.{op}", target_kind="vendor_device", target_id=device_id,
        detail={k: v for k, v in kw.items()},
    )
    await session.commit()


@router.post("/ui/tado/zone/{device_id}/set-temp")
async def ui_tado_zone_temp(
    device_id: str,
    request: Request,
    celsius: float = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if not (5.0 <= celsius <= 30.0):
        raise HTTPException(400, "temperature must be 5–30 °C")
    await _zone_op(session, actor, device_id, "temp", celsius=celsius)
    request.session["tado_flash"] = {"kind": "emerald", "msg": f"Set {celsius:g} °C."}
    return RedirectResponse("/ui/tado", status_code=303)


@router.post("/ui/tado/zone/{device_id}/off")
async def ui_tado_zone_off(
    device_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    await _zone_op(session, actor, device_id, "off")
    request.session["tado_flash"] = {"kind": "amber", "msg": "Zone turned off."}
    return RedirectResponse("/ui/tado", status_code=303)


@router.post("/ui/tado/zone/{device_id}/resume")
async def ui_tado_zone_resume(
    device_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    await _zone_op(session, actor, device_id, "resume")
    request.session["tado_flash"] = {"kind": "sky", "msg": "Resumed schedule."}
    return RedirectResponse("/ui/tado", status_code=303)


# ── whole-home presence ──────────────────────────────────────────────
@router.post("/ui/tado/{account_id}/presence")
async def ui_tado_presence(
    account_id: str,
    request: Request,
    home_id: str = Form(...),
    presence: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    account = await session.get(VendorAccount, account_id)
    if account is None or account.provider != "tado":
        raise HTTPException(404, "tado account not found")
    rotated: list = []
    client = _client_for(account, rotated)
    try:
        await client.set_presence(home_id, presence)
    except Exception as e:
        raise HTTPException(502, f"Tado presence failed: {e}")
    _persist_rotation(account, rotated)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="tado.presence", target_kind="vendor_account", target_id=account_id,
        detail={"home_id": home_id, "presence": presence.upper()},
    )
    await session.commit()
    request.session["tado_flash"] = {"kind": "sky", "msg": f"Presence → {presence.upper()}."}
    return RedirectResponse("/ui/tado", status_code=303)


# ── bulk control across ALL accounts ─────────────────────────────────
@router.post("/ui/tado/all/{action}", response_class=JSONResponse)
async def ui_tado_all(
    action: str,
    request: Request,
    celsius: float = Form(20.0),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    """Apply one action to EVERY zone on EVERY tado account.
    action: off | resume | set-temp (uses `celsius`)."""
    if action == "set-temp" and not (5.0 <= celsius <= 30.0):
        raise HTTPException(400, "temperature must be 5–30 °C")
    accounts = await _tado_accounts(session)
    report = {"action": action, "homes": 0, "zones_ok": 0, "zones_failed": 0, "errors": []}
    for account in accounts:
        if not account.refresh_token_encrypted or account.status != "active":
            continue
        rotated: list = []
        client = _client_for(account, rotated)
        zones = (await session.execute(
            select(VendorDevice).where(VendorDevice.vendor_account_id == account.id)
        )).scalars().all()
        for z in zones:
            try:
                home_id, zone_id = _zone_addr(z)
                if action == "off":
                    await client.set_zone_off(home_id, zone_id)
                elif action == "resume":
                    await client.resume_schedule(home_id, zone_id)
                elif action == "set-temp":
                    await client.set_zone_temperature(home_id, zone_id, celsius)
                else:
                    raise HTTPException(400, f"unknown bulk action {action}")
                report["zones_ok"] += 1
            except HTTPException:
                raise
            except Exception as e:
                report["zones_failed"] += 1
                report["errors"].append(f"{z.name}: {str(e)[:120]}")
        _persist_rotation(account, rotated)
        report["homes"] += 1
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=f"tado.all.{action}", target_kind="vendor_account", target_id="*",
        detail=report,
    )
    await session.commit()
    return report
