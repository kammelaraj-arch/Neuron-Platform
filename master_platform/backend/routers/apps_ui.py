"""Independent Apps catalogue + deploy.

Catalogue of standalone applications the platform can publish + push
to devices. Each app row carries enough metadata for an operator to
understand what it is (title, description, app_type, vendor, icon,
tags, target compute compatibility) and enough operational metadata
for the platform to install + autostart it (repo_url / download_url,
install_command, start_command, autostart_method, autostart_unit
template).

Deploys land via the existing SSH channel established on each
EdgeGroup. The Pi-target path:
    1. Decrypt SSH creds from the group's stored secrets.
    2. asyncssh-connect.
    3. Run the app's install_command.
    4. Drop the autostart_unit_template into /etc/systemd/system/.
    5. systemctl daemon-reload + enable --now.
    6. Verify the unit is active, audit, record an IndependentAppDeploy.

Non-Pi targets (windows / android / etc.) are catalogue-only for now —
the operator downloads the app and installs it on the target manually.
We catalogue them so the fleet still has a single inventory + can
audit which devices got which app.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import (
    APP_STATUSES, APP_TYPES, AUTOSTART_METHODS,
    APIKey, EdgeGroup,
    IndependentApp, IndependentAppDeploy,
)
from ..security.audit import record
from ..security.ui_auth import ui_require_admin, ui_require_login

_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))
log = logging.getLogger("neuron.apps_ui")

router = APIRouter(tags=["ui-apps"])


def _next_short_id(rows: list[IndependentApp]) -> str:
    max_n = 0
    for r in rows:
        m = re.match(r"^APP-(\d+)$", r.short_id or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"APP-{max_n + 1:04d}"


# ─── List + new form ────────────────────────────────────────────────────
@router.get("/ui/apps", response_class=HTMLResponse)
async def ui_apps_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    rows = (await session.execute(
        select(IndependentApp).order_by(IndependentApp.created_at.desc())
    )).scalars().all()
    flash = request.session.pop("apps_flash", None)
    return templates.TemplateResponse(
        "apps.html",
        {
            "request": request,
            "apps": rows,
            "app_types": APP_TYPES,
            "app_statuses": APP_STATUSES,
            "autostart_methods": AUTOSTART_METHODS,
            "flash": flash,
            "signed_in": True,
        },
    )


@router.post("/ui/apps/new")
async def ui_apps_new(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    app_type: str = Form("pi"),
    version: str = Form("1.0.0"),
    vendor: str = Form(""),
    icon_url: str = Form(""),
    repo_url: str = Form(""),
    download_url: str = Form(""),
    install_command: str = Form(""),
    start_command: str = Form(""),
    autostart_method: str = Form("systemd"),
    autostart_unit_template: str = Form(""),
    compatible_compute: str = Form(""),  # CSV
    tags: str = Form(""),                # CSV
    status: str = Form("draft"),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if app_type not in APP_TYPES:
        raise HTTPException(400, f"app_type must be one of {APP_TYPES}")
    if status not in APP_STATUSES:
        raise HTTPException(400, f"status must be one of {APP_STATUSES}")
    if autostart_method not in AUTOSTART_METHODS:
        raise HTTPException(400, f"autostart_method must be one of {AUTOSTART_METHODS}")
    if not title.strip():
        raise HTTPException(400, "title is required")

    existing = (await session.execute(select(IndependentApp))).scalars().all()
    short = _next_short_id(existing)
    row = IndependentApp(
        short_id=short,
        title=title.strip(),
        description=description.strip() or None,
        app_type=app_type,
        version=version.strip() or "1.0.0",
        vendor=vendor.strip() or None,
        icon_url=icon_url.strip() or None,
        repo_url=repo_url.strip() or None,
        download_url=download_url.strip() or None,
        install_command=install_command.strip() or None,
        start_command=start_command.strip() or None,
        autostart_method=autostart_method,
        autostart_unit_template=autostart_unit_template.strip() or None,
        compatible_compute_json=[c.strip() for c in compatible_compute.split(",") if c.strip()],
        tags_json=[t.strip() for t in tags.split(",") if t.strip()],
        status=status,
        notes=notes.strip() or None,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="app.create", target_kind="independent_app", target_id=row.id,
        detail={"short_id": short, "title": row.title, "app_type": app_type, "version": row.version},
    )
    await session.commit()
    request.session["apps_flash"] = {"kind": "emerald", "msg": f"App {short} ({row.title}) created."}
    return RedirectResponse("/ui/apps", status_code=303)


@router.post("/ui/apps/{app_id}/status")
async def ui_apps_set_status(
    app_id: str,
    request: Request,
    status: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if status not in APP_STATUSES:
        raise HTTPException(400, f"status must be one of {APP_STATUSES}")
    row = await session.get(IndependentApp, app_id)
    if row is None:
        raise HTTPException(404, "app not found")
    prev = row.status
    row.status = status
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="app.set_status", target_kind="independent_app", target_id=row.id,
        detail={"from": prev, "to": status},
    )
    await session.commit()
    return RedirectResponse("/ui/apps", status_code=303)


@router.post("/ui/apps/{app_id}/delete")
async def ui_apps_delete(
    app_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(IndependentApp, app_id)
    if row is None:
        raise HTTPException(404, "app not found")
    label = f"{row.short_id} ({row.title})"
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="app.delete", target_kind="independent_app", target_id=app_id,
    )
    await session.commit()
    request.session["apps_flash"] = {"kind": "red", "msg": f"Deleted {label}."}
    return RedirectResponse("/ui/apps", status_code=303)


# ─── Deploy to a group via SSH ───────────────────────────────────────────
@router.post("/ui/apps/{app_id}/deploy/{group_id}", response_class=JSONResponse)
async def ui_apps_deploy_to_group(
    app_id: str,
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Push the app onto an EdgeGroup over SSH and register systemd
    autostart. Pi targets only — non-Pi apps are catalogue-only."""
    from ..deploy_worker import deploy_bundle_via_ssh   # reuse the connect helper
    import asyncssh

    app_row = await session.get(IndependentApp, app_id)
    if app_row is None:
        raise HTTPException(404, "app not found")
    group = await session.get(EdgeGroup, group_id)
    if group is None:
        raise HTTPException(404, "group not found")
    if app_row.app_type not in ("pi", "linux", "docker"):
        return {"ok": False, "summary": f"App type '{app_row.app_type}' is catalogue-only; deploy manually on the target."}
    if not app_row.install_command:
        return {"ok": False, "summary": "App has no install_command set."}
    if not group.ssh_host and not group.hostname and not group.local_ip:
        return {"ok": False, "summary": "Group has no SSH host configured."}

    # Decrypt SSH creds.
    from ..security.secret_crypto import decrypt_secret
    pwd     = decrypt_secret(group.ssh_password_encrypted)     if group.ssh_password_encrypted     else None
    key_pem = decrypt_secret(group.ssh_private_key_encrypted)  if group.ssh_private_key_encrypted  else None
    sudo_pw = decrypt_secret(group.sudo_password_encrypted)    if group.sudo_password_encrypted    else None

    host = group.ssh_host or group.hostname or group.local_ip
    port = int(group.ssh_port or 22)
    user = group.ssh_username or "pi"

    connect_kwargs: dict = {
        "host": host, "port": port, "username": user,
        "known_hosts": None, "connect_timeout": 15,
    }
    if key_pem:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(key_pem)]
    if pwd:
        connect_kwargs["password"] = pwd

    steps: list[dict] = []
    async def _run(conn, cmd: str, become_root: bool = False, timeout: int = 300):
        if become_root and user != "root":
            cmd_q = "'" + cmd.replace("'", "'\\''") + "'"
            cmd = f"sudo -S -p '' bash -lc {cmd_q}"
            r = await conn.run(cmd, input=(sudo_pw or pwd or "") + "\n", check=False, timeout=timeout)
        else:
            r = await conn.run(cmd, check=False, timeout=timeout)
        return r.exit_status or 0, (r.stdout or "").strip(), (r.stderr or "").strip()

    unit_name = re.sub(r"[^a-z0-9-]+", "-", app_row.short_id.lower()).strip("-")
    unit_path = f"/etc/systemd/system/neuron-app-{unit_name}.service"

    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            steps.append({"name": "ssh_connect", "ok": True, "detail": f"{user}@{host}:{port}"})

            # 1. Install
            rc, out, err = await _run(conn, app_row.install_command, become_root=True, timeout=900)
            steps.append({"name": "install_command", "ok": rc == 0,
                          "detail": (out + "\n" + err)[-800:]})
            if rc != 0:
                raise RuntimeError(f"install_command exit {rc}")

            # 2. Write systemd unit
            unit_body = app_row.autostart_unit_template
            if not unit_body and app_row.start_command:
                # Generate a minimal unit from start_command.
                unit_body = (
                    "[Unit]\n"
                    f"Description={app_row.title}\n"
                    "After=network-online.target\n"
                    "Wants=network-online.target\n\n"
                    "[Service]\n"
                    "Type=simple\n"
                    f"ExecStart={app_row.start_command}\n"
                    "Restart=on-failure\n"
                    "RestartSec=5\n"
                    "User=root\n"
                    "StandardOutput=journal\n"
                    "StandardError=journal\n\n"
                    "[Install]\n"
                    "WantedBy=multi-user.target\n"
                )
            if app_row.autostart_method == "systemd" and unit_body:
                # Heredoc the unit to the target path.
                escaped = unit_body.replace("$", r"\$")
                quoted = "'" + escaped.replace("'", "'\\''") + "'"
                rc, out, err = await _run(
                    conn,
                    f"printf '%s' {quoted} > {unit_path} && systemctl daemon-reload",
                    become_root=True,
                )
                steps.append({"name": "write_systemd_unit", "ok": rc == 0,
                              "detail": (out + "\n" + err)[-400:]})

                rc, out, err = await _run(conn,
                    f"systemctl enable --now neuron-app-{unit_name}.service",
                    become_root=True)
                steps.append({"name": "systemd_enable_now", "ok": rc == 0,
                              "detail": (out + "\n" + err)[-400:]})

                # Verify
                rc, out, err = await _run(conn,
                    f"systemctl is-active neuron-app-{unit_name}.service",
                    become_root=False)
                active = out.strip() == "active"
                steps.append({"name": "autostart_active", "ok": active, "detail": out})

        ok = all(s["ok"] for s in steps)
        dep = IndependentAppDeploy(
            app_id=app_row.id, group_id=group.id,
            version=app_row.version,
            status="ok" if ok else "failed",
            detail=(steps[-1]["detail"] if steps else "")[:1000],
        )
        session.add(dep)
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="app.deploy", target_kind="independent_app", target_id=app_row.id,
            detail={"group_id": group.id, "ok": ok,
                    "steps": [{"name": s["name"], "ok": s["ok"]} for s in steps]},
        )
        await session.commit()
        return {"ok": ok, "host": host, "unit": unit_name,
                "summary": "installed + autostart enabled" if ok else "deploy failed (see steps)",
                "steps": steps}
    except Exception as e:
        log.exception("app deploy failed")
        await session.rollback()
        return {"ok": False, "summary": f"{type(e).__name__}: {str(e)[:200]}", "steps": steps}


# ─── Deploy history ──────────────────────────────────────────────────────
@router.get("/ui/apps/{app_id}/history", response_class=JSONResponse)
async def ui_apps_history(
    app_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    rows = (await session.execute(
        select(IndependentAppDeploy).where(IndependentAppDeploy.app_id == app_id)
        .order_by(IndependentAppDeploy.deployed_at.desc())
    )).scalars().all()
    return [
        {
            "id": r.id, "group_id": r.group_id, "version": r.version,
            "status": r.status, "detail": (r.detail or "")[:300],
            "deployed_at": r.deployed_at.isoformat(),
        }
        for r in rows
    ]
