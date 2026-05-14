from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, VENDOR_PROVIDERS, VendorAccount, VendorDevice
from ..security.audit import record
from ..security.secret_crypto import encrypt_secret
from ..security.ui_auth import ui_require_admin
from ..vendor_discovery import discover_devices as _discover

_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-vendor-accounts"])


@router.get("/ui/vendor-accounts", response_class=HTMLResponse)
async def ui_vendor_accounts(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    rows = (await session.execute(
        select(VendorAccount).order_by(VendorAccount.provider, VendorAccount.label)
    )).scalars().all()
    # Bucket devices by account so the template renders each account's
    # device list inline beneath the account row.
    devices_by_account: dict[str, list[VendorDevice]] = {}
    if rows:
        all_devices = (await session.execute(
            select(VendorDevice).order_by(VendorDevice.name)
        )).scalars().all()
        for d in all_devices:
            devices_by_account.setdefault(d.vendor_account_id, []).append(d)
    flash = request.session.pop("vendor_accounts_flash", None)
    return templates.TemplateResponse(
        "vendor_accounts.html",
        {
            "request": request,
            "accounts": rows,
            "devices_by_account": devices_by_account,
            "providers": VENDOR_PROVIDERS,
            "flash": flash,
            "signed_in": True,
        },
    )


@router.post("/ui/vendor-accounts/{account_id}/discover", response_class=JSONResponse)
async def ui_vendor_accounts_discover(
    account_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    """Call the vendor's API to list devices on this account; upsert
    VendorDevice rows. Returns a per-action summary."""
    row = await session.get(VendorAccount, account_id)
    if row is None:
        raise HTTPException(404, "account not found")
    result = await _discover(session, row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="vendor_account.discover", target_kind="vendor_account", target_id=row.id,
        detail={"ok": result["ok"], "discovered": result["discovered"],
                "added": result["added"], "updated": result["updated"],
                "errors": result["errors"]},
    )
    await session.commit()
    return result


@router.post("/ui/vendor-accounts/{account_id}/devices/add")
async def ui_vendor_accounts_add_device(
    account_id: str,
    request: Request,
    vendor_device_id: str = Form(...),
    name: str = Form(...),
    model: str = Form(""),
    device_type: str = Form(""),
    ip_local: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    """Manual add — for vendors without discovery, or operator pre-
    seeding a device before bringing it online."""
    parent = await session.get(VendorAccount, account_id)
    if parent is None:
        raise HTTPException(404, "account not found")
    if not vendor_device_id.strip() or not name.strip():
        raise HTTPException(400, "vendor_device_id and name are required")
    # Upsert by (account, vendor_device_id).
    existing = (await session.execute(
        select(VendorDevice).where(
            VendorDevice.vendor_account_id == account_id,
            VendorDevice.vendor_device_id == vendor_device_id.strip(),
        )
    )).scalar_one_or_none()
    if existing is None:
        row = VendorDevice(
            vendor_account_id=account_id,
            vendor_device_id=vendor_device_id.strip(),
            name=name.strip(),
            model=model.strip() or None,
            device_type=device_type.strip() or None,
            ip_local=ip_local.strip() or None,
        )
        session.add(row)
        action = "vendor_device.create"
    else:
        existing.name = name.strip()
        existing.model = model.strip() or existing.model
        existing.device_type = device_type.strip() or existing.device_type
        existing.ip_local = ip_local.strip() or existing.ip_local
        row = existing
        action = "vendor_device.update"
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=action, target_kind="vendor_device", target_id=row.id,
        detail={"vendor_device_id": vendor_device_id, "provider": parent.provider},
    )
    await session.commit()
    request.session["vendor_accounts_flash"] = {
        "kind": "emerald", "msg": f"{parent.provider}: device '{row.name}' saved."
    }
    return RedirectResponse("/ui/vendor-accounts", status_code=303)


@router.post("/ui/vendor-accounts/{account_id}/devices/{device_id}/delete")
async def ui_vendor_accounts_delete_device(
    account_id: str,
    device_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(VendorDevice, device_id)
    if row is None or row.vendor_account_id != account_id:
        raise HTTPException(404, "device not found in this account")
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="vendor_device.delete", target_kind="vendor_device", target_id=device_id,
    )
    await session.commit()
    return RedirectResponse("/ui/vendor-accounts", status_code=303)


@router.post("/ui/vendor-accounts/new")
async def ui_vendor_accounts_new(
    request: Request,
    provider: str = Form(...),
    label: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    api_key: str = Form(""),
    refresh_token: str = Form(""),
    region: str = Form(""),
    base_url: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    provider_clean = (provider or "").strip().lower()
    if provider_clean not in VENDOR_PROVIDERS:
        raise HTTPException(400, f"provider must be one of {VENDOR_PROVIDERS}")
    if not label.strip():
        raise HTTPException(400, "label is required")

    row = VendorAccount(
        provider=provider_clean,
        label=label.strip(),
        username=username.strip() or None,
        password_encrypted=encrypt_secret(password) if password.strip() else None,
        api_key_encrypted=encrypt_secret(api_key) if api_key.strip() else None,
        refresh_token_encrypted=encrypt_secret(refresh_token) if refresh_token.strip() else None,
        region=region.strip() or None,
        base_url=base_url.strip() or None,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="vendor_account.create", target_kind="vendor_account", target_id=row.id,
        detail={
            "provider": row.provider, "label": row.label, "username": row.username,
            "has_password": bool(row.password_encrypted),
            "has_api_key": bool(row.api_key_encrypted),
            "has_refresh_token": bool(row.refresh_token_encrypted),
            "region": row.region, "base_url": row.base_url,
        },
    )
    await session.commit()
    request.session["vendor_accounts_flash"] = {
        "kind": "emerald", "msg": f"{row.provider}: {row.label} stored."
    }
    return RedirectResponse("/ui/vendor-accounts", status_code=303)


@router.post("/ui/vendor-accounts/{account_id}/rotate")
async def ui_vendor_accounts_rotate(
    account_id: str,
    request: Request,
    password: str = Form(""),
    api_key: str = Form(""),
    refresh_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(VendorAccount, account_id)
    if row is None:
        raise HTTPException(404, "account not found")
    changed: list[str] = []
    if password.strip():
        row.password_encrypted = encrypt_secret(password)
        changed.append("password")
    if api_key.strip():
        row.api_key_encrypted = encrypt_secret(api_key)
        changed.append("api_key")
    if refresh_token.strip():
        row.refresh_token_encrypted = encrypt_secret(refresh_token)
        changed.append("refresh_token")
    if not changed:
        raise HTTPException(400, "supply at least one new secret to rotate")
    row.rotated_at = datetime.now(timezone.utc)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="vendor_account.rotate", target_kind="vendor_account", target_id=row.id,
        detail={"changed": changed},
    )
    await session.commit()
    request.session["vendor_accounts_flash"] = {
        "kind": "emerald", "msg": f"Rotated: {', '.join(changed)}."
    }
    return RedirectResponse("/ui/vendor-accounts", status_code=303)


@router.post("/ui/vendor-accounts/{account_id}/revoke")
async def ui_vendor_accounts_revoke(
    account_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(VendorAccount, account_id)
    if row is None:
        raise HTTPException(404, "account not found")
    row.status = "revoked"
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="vendor_account.revoke", target_kind="vendor_account", target_id=row.id,
    )
    await session.commit()
    request.session["vendor_accounts_flash"] = {
        "kind": "amber", "msg": f"Revoked {row.provider}: {row.label}."
    }
    return RedirectResponse("/ui/vendor-accounts", status_code=303)


@router.post("/ui/vendor-accounts/{account_id}/delete")
async def ui_vendor_accounts_delete(
    account_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(VendorAccount, account_id)
    if row is None:
        raise HTTPException(404, "account not found")
    label = f"{row.provider}: {row.label}"
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="vendor_account.delete", target_kind="vendor_account", target_id=account_id,
    )
    await session.commit()
    request.session["vendor_accounts_flash"] = {"kind": "red", "msg": f"Deleted {label}."}
    return RedirectResponse("/ui/vendor-accounts", status_code=303)
