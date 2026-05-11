from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import SessionLocal, init_db
from .library_loader import load_catalog
from .models import APIKey
from .routers import (
    ai_agent,
    apikeys,
    audit,
    auth_ui,
    devices,
    fabric,
    library,
    library_manage_ui,
    mtls,
    ota,
    processes,
    recipes,
    secrets_ui,
    sysops_ui,
    systems,
    twin_push,
    ui,
)
from .security.keys import issue_payload
from .security.ui_auth import UILoginRequired, UIPermissionDenied


_log = logging.getLogger("neuron.master")


def _ensure_session_secret() -> str:
    """Persist a session-cookie signing secret to disk on first boot."""
    p = Path("data/.session_secret")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    s = secrets.token_urlsafe(48)
    p.write_text(s, encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return s


def _write_bootstrap_key_file(secret: str) -> Path:
    """Write the one-time bootstrap admin key to a 0600 file."""
    p = Path("data/bootstrap_admin.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# Bootstrap admin API key for Neuron Master Platform.\n"
        "# Created at first run. Open the Master at /login, paste this\n"
        "# value, then DELETE this file. After that, manage every key\n"
        "# from the in-app Secrets section at /ui/secrets.\n"
        f"{secret}\n",
        encoding="utf-8",
    )
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


async def _bootstrap_admin_key_if_needed() -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(select(APIKey).where(APIKey.tier == "admin"))
        if existing is not None:
            return
        secret, kw = issue_payload(
            label="bootstrap-admin",
            owner="bootstrap",
            tier="admin",
            scopes=["admin"],
            rate_per_minute=600,
            rate_burst=200,
            ttl_days=None,
        )
        session.add(APIKey(**kw))
        await session.commit()
        path = _write_bootstrap_key_file(secret)
        _log.warning(
            "BOOTSTRAP ADMIN API KEY written to %s (chmod 0600). "
            "Open /login, paste it, then delete the file.",
            path.resolve(),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.build_artifacts_dir).mkdir(parents=True, exist_ok=True)
    Path("data").mkdir(parents=True, exist_ok=True)
    await init_db()
    load_catalog(force=True)
    await _bootstrap_admin_key_if_needed()

    # Periodic audit retention prune so SQLite doesn't grow unbounded.
    from .security.audit_retention import retention_loop
    stop_event = asyncio.Event()
    retention_task = asyncio.create_task(retention_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(retention_task, timeout=2.0)
        except asyncio.TimeoutError:
            retention_task.cancel()


app = FastAPI(
    title="Neuron Platform — Master",
    version="0.3.0",
    description="Unified Master Platform: Admin + Build + Config + Library Registry + API Mgmt + Secrets + Library Mgmt.",
    lifespan=lifespan,
)

# Session cookie for the browser UI. Secret is persisted to disk so cookies
# survive restarts. Cookies do not contain the API key plaintext — only its id.
app.add_middleware(
    SessionMiddleware,
    secret_key=_ensure_session_secret(),
    same_site="lax",
    https_only=False,
    session_cookie="neuron_session",
    max_age=60 * 60 * 12,  # 12h
)


@app.exception_handler(UILoginRequired)
async def _ui_login_redirect(request: Request, exc: UILoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(UIPermissionDenied)
async def _ui_admin_redirect(request: Request, exc: UIPermissionDenied):
    return RedirectResponse("/?error=admin_required", status_code=303)


_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# UI routers
app.include_router(auth_ui.router)
app.include_router(ui.router)
app.include_router(secrets_ui.router)
app.include_router(library_manage_ui.router)
app.include_router(sysops_ui.router)

# JSON API routers
app.include_router(library.router)
app.include_router(systems.router)
app.include_router(devices.router)
app.include_router(processes.router)
app.include_router(apikeys.router)
app.include_router(audit.router)
app.include_router(ota.router)
app.include_router(recipes.router)
app.include_router(ai_agent.router)
app.include_router(twin_push.router)
app.include_router(fabric.router)
app.include_router(mtls.router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict:
    catalog = load_catalog()
    return {
        "status": "ok",
        "library_items": len(catalog.by_id),
        "version": app.version,
    }
