"""Ring control UI + endpoints.

Sits on top of the VendorAccount framework (provider="ring") and the
existing _discover_ring driver. Lets the operator drive every Ring
device across every connected account: floodlights, sirens, chime
test, alarm-hub mode, and bulk actions ("all sirens off", "arm
everything away").

CLOUD integration — Master level only. Ring cameras / sirens are
advisory; the local-brain failsafe contract does NOT extend to them.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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

router = APIRouter(tags=["ui-ring"])


# ── helpers ──────────────────────────────────────────────────────────
def _client_for(account: VendorAccount, rotated_holder: list):
    from ..integrations.ring import RingClient
    if not account.refresh_token_encrypted:
        raise HTTPException(400,
            f"Ring account {account.label!r} has no refresh token. "
            "Generate one with `pip install ring-doorbell; ring-doorbell "
            "auth-token --username <email>` and paste it on the Vendor "
            "accounts page.")
    refresh = decrypt_secret(account.refresh_token_encrypted)
    extra = dict(account.extra_json or {})
    return RingClient(
        refresh_token=refresh,
        hardware_id=extra.get("hardware_id"),
        on_token_refresh=lambda tok: rotated_holder.append(tok),
    )


def _persist_rotation(account: VendorAccount, rotated_holder: list) -> None:
    if rotated_holder:
        account.refresh_token_encrypted = encrypt_secret(rotated_holder[-1])


async def _ring_accounts(session: AsyncSession) -> list[VendorAccount]:
    return (await session.execute(
        select(VendorAccount)
        .where(VendorAccount.provider == "ring")
        .order_by(VendorAccount.label)
    )).scalars().all()


def _account_for_device(session_devices: dict, device: VendorDevice) -> str:
    return device.vendor_account_id


# ── page ─────────────────────────────────────────────────────────────
@router.get("/ui/ring", response_class=HTMLResponse)
async def ui_ring(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    accounts = await _ring_accounts(session)
    devices_by_account: dict[str, list[VendorDevice]] = {}
    if accounts:
        ids = [a.id for a in accounts]
        all_devices = (await session.execute(
            select(VendorDevice)
            .where(VendorDevice.vendor_account_id.in_(ids))
            .order_by(VendorDevice.name)
        )).scalars().all()
        for d in all_devices:
            devices_by_account.setdefault(d.vendor_account_id, []).append(d)
    flash = request.session.pop("ring_flash", None)
    return templates.TemplateResponse(
        "ring.html",
        {
            "request": request,
            "accounts": accounts,
            "devices_by_account": devices_by_account,
            "connected": {a.id: bool(a.refresh_token_encrypted) for a in accounts},
            "flash": flash,
            "signed_in": True,
        },
    )


# ── per-device control ───────────────────────────────────────────────
async def _device_op(session, actor, device_id: str, op: str, **kw):
    device = await session.get(VendorDevice, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    account = await session.get(VendorAccount, device.vendor_account_id)
    if account is None or account.provider != "ring":
        raise HTTPException(404, "ring account not found")
    rotated: list = []
    client = _client_for(account, rotated)
    vid = device.vendor_device_id
    try:
        if op == "floodlight_on":
            await client.floodlight_on(vid)
        elif op == "floodlight_off":
            await client.floodlight_off(vid)
        elif op == "siren_on":
            await client.siren_on(vid, duration_s=kw.get("duration_s", 30))
        elif op == "siren_off":
            await client.siren_off(vid)
        elif op == "play_chime":
            await client.play_chime(vid, kind=kw.get("kind", "ding"))
        else:
            raise HTTPException(400, f"unknown op {op}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Ring control failed: {e}")
    _persist_rotation(account, rotated)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=f"ring.device.{op}", target_kind="vendor_device", target_id=device_id,
        detail={k: v for k, v in kw.items()},
    )
    await session.commit()


@router.post("/ui/ring/device/{device_id}/floodlight/on")
async def ui_ring_floodlight_on(device_id: str, request: Request,
                                session: AsyncSession = Depends(get_session),
                                actor: APIKey = Depends(ui_require_admin)):
    await _device_op(session, actor, device_id, "floodlight_on")
    request.session["ring_flash"] = {"kind": "emerald", "msg": "Floodlight on."}
    return RedirectResponse("/ui/ring", status_code=303)


@router.post("/ui/ring/device/{device_id}/floodlight/off")
async def ui_ring_floodlight_off(device_id: str, request: Request,
                                 session: AsyncSession = Depends(get_session),
                                 actor: APIKey = Depends(ui_require_admin)):
    await _device_op(session, actor, device_id, "floodlight_off")
    request.session["ring_flash"] = {"kind": "sky", "msg": "Floodlight off."}
    return RedirectResponse("/ui/ring", status_code=303)


@router.post("/ui/ring/device/{device_id}/siren/on")
async def ui_ring_siren_on(device_id: str, request: Request,
                           duration_s: int = Form(30),
                           session: AsyncSession = Depends(get_session),
                           actor: APIKey = Depends(ui_require_admin)):
    if not (5 <= duration_s <= 300):
        raise HTTPException(400, "duration_s must be 5–300")
    await _device_op(session, actor, device_id, "siren_on", duration_s=duration_s)
    request.session["ring_flash"] = {"kind": "red", "msg": f"Siren on for {duration_s}s."}
    return RedirectResponse("/ui/ring", status_code=303)


@router.post("/ui/ring/device/{device_id}/siren/off")
async def ui_ring_siren_off(device_id: str, request: Request,
                            session: AsyncSession = Depends(get_session),
                            actor: APIKey = Depends(ui_require_admin)):
    await _device_op(session, actor, device_id, "siren_off")
    request.session["ring_flash"] = {"kind": "sky", "msg": "Siren off."}
    return RedirectResponse("/ui/ring", status_code=303)


@router.post("/ui/ring/chime/{device_id}/play")
async def ui_ring_chime(device_id: str, request: Request,
                        kind: str = Form("ding"),
                        session: AsyncSession = Depends(get_session),
                        actor: APIKey = Depends(ui_require_admin)):
    await _device_op(session, actor, device_id, "play_chime", kind=kind)
    request.session["ring_flash"] = {"kind": "emerald", "msg": f"Chime '{kind}' played."}
    return RedirectResponse("/ui/ring", status_code=303)


@router.get("/ui/ring/device/{device_id}/snapshot")
async def ui_ring_snapshot(device_id: str,
                           session: AsyncSession = Depends(get_session),
                           actor: APIKey = Depends(ui_require_admin)) -> Response:
    """Most-recent cached snapshot JPEG. Streams the bytes Ring serves
    so we don't store imagery on Neuron."""
    device = await session.get(VendorDevice, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    account = await session.get(VendorAccount, device.vendor_account_id)
    if account is None or account.provider != "ring":
        raise HTTPException(404, "ring account not found")
    rotated: list = []
    client = _client_for(account, rotated)
    try:
        img = await client.snapshot_bytes(device.vendor_device_id)
    except Exception as e:
        raise HTTPException(502, f"Ring snapshot failed: {e}")
    _persist_rotation(account, rotated)
    await session.commit()
    return Response(content=img, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


# ── alarm hub mode ───────────────────────────────────────────────────
@router.post("/ui/ring/{account_id}/alarm")
async def ui_ring_alarm(
    account_id: str,
    request: Request,
    location_id: str = Form(...),
    mode: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    account = await session.get(VendorAccount, account_id)
    if account is None or account.provider != "ring":
        raise HTTPException(404, "ring account not found")
    rotated: list = []
    client = _client_for(account, rotated)
    try:
        await client.set_alarm_mode(location_id, mode)
    except Exception as e:
        raise HTTPException(502, f"Ring alarm-mode failed: {e}")
    _persist_rotation(account, rotated)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="ring.alarm.mode", target_kind="vendor_account", target_id=account_id,
        detail={"location_id": location_id, "mode": mode.lower()},
    )
    await session.commit()
    pretty = {"all": "Away (all)", "home": "Home", "none": "Disarmed"}.get(
        mode.lower(), mode.upper())
    request.session["ring_flash"] = {"kind": "amber", "msg": f"Alarm → {pretty}."}
    return RedirectResponse("/ui/ring", status_code=303)


# ── bulk control across ALL accounts ─────────────────────────────────
_BULK_OPS = {
    "lights-off":   ("floodlight_off", "floodlight"),
    "sirens-off":   ("siren_off",      "siren"),
    "alarm-away":   ("alarm_all",      "alarm_hub"),
    "alarm-home":   ("alarm_home",     "alarm_hub"),
    "alarm-disarm": ("alarm_none",     "alarm_hub"),
}


@router.post("/ui/ring/all/{action}", response_class=JSONResponse)
async def ui_ring_all(
    action: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    """Apply one action to every matching Ring device on every account.
    Filters by device_type so we don't try to siren a chime, etc."""
    if action not in _BULK_OPS:
        raise HTTPException(400, f"action must be one of {list(_BULK_OPS)}")
    op, filter_type = _BULK_OPS[action]
    accounts = await _ring_accounts(session)
    report = {"action": action, "accounts": 0,
              "devices_ok": 0, "devices_failed": 0, "errors": []}
    for account in accounts:
        if not account.refresh_token_encrypted or account.status != "active":
            continue
        rotated: list = []
        try:
            client = _client_for(account, rotated)
        except HTTPException:
            continue
        devices = (await session.execute(
            select(VendorDevice).where(VendorDevice.vendor_account_id == account.id)
        )).scalars().all()
        if op.startswith("alarm_"):
            mode = {"alarm_all": "all", "alarm_home": "home",
                    "alarm_none": "none"}[op]
            try:
                for loc in await client.list_locations():
                    await client.set_alarm_mode(loc.get("location_id"), mode)
                    report["devices_ok"] += 1
            except Exception as e:
                report["devices_failed"] += 1
                report["errors"].append(f"{account.label}: {str(e)[:120]}")
        else:
            for d in devices:
                if filter_type and (d.device_type or "").lower() not in (filter_type, "camera", "doorbell"):
                    continue
                try:
                    if op == "floodlight_off":
                        await client.floodlight_off(d.vendor_device_id)
                    elif op == "siren_off":
                        await client.siren_off(d.vendor_device_id)
                    report["devices_ok"] += 1
                except Exception as e:
                    report["devices_failed"] += 1
                    report["errors"].append(f"{d.name}: {str(e)[:120]}")
        _persist_rotation(account, rotated)
        report["accounts"] += 1
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=f"ring.all.{action}", target_kind="vendor_account", target_id="*",
        detail=report,
    )
    await session.commit()
    return report
