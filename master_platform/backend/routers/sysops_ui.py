"""Neuron-scoped sys-ops dashboard.

This page intentionally only reads Neuron's own state (its own SQLite
DB, its own data dir, its own configured Edges via the
edge_systems.address). It does NOT call docker, does NOT inspect
ShitalEco containers or services, does NOT proxy anything outside the
Neuron stack.

Visible to admin-tier sessions only.
"""
from __future__ import annotations

import asyncio
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..library_loader import load_catalog
from ..models import (APIKey, AuditEvent, Device, EdgeCert, EdgeSystem,
                       FirmwareRelease, NodeSystem, RootSystem)
from ..security import mtls, signing
from ..security.ui_auth import ui_require_admin


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-sysops"])

# Captured at first import so we can show "Master uptime" honestly.
_PROCESS_BOOT_AT = time.monotonic()


def _human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TiB"


def _dir_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for entry in p.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


async def _ping_edge(address: str | None, timeout_s: float = 1.5) -> dict[str, Any]:
    if not address:
        return {"online": False, "reason": "no address configured"}
    url = address.rstrip("/") + "/healthz"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return {"online": False, "reason": f"HTTP {resp.status_code}"}
        try:
            doc = resp.json()
        except Exception:  # noqa: BLE001
            doc = {}
        return {"online": True, "info": doc}
    except httpx.HTTPError as exc:
        return {"online": False, "reason": f"unreachable ({exc.__class__.__name__})"}


@router.get("/ui/sysops", response_class=HTMLResponse)
async def ui_sysops(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    catalog = load_catalog()

    # ── Topology counts (Neuron's own DB only) ───────────────────────────────
    n_roots = await session.scalar(select(func.count()).select_from(RootSystem)) or 0
    n_nodes = await session.scalar(select(func.count()).select_from(NodeSystem)) or 0
    n_edges = await session.scalar(select(func.count()).select_from(EdgeSystem)) or 0
    n_devices = await session.scalar(select(func.count()).select_from(Device)) or 0
    n_releases = await session.scalar(select(func.count()).select_from(FirmwareRelease)) or 0

    # ── Edge reachability (only Neuron-configured Edges) ────────────────────
    edges = (await session.execute(select(EdgeSystem).order_by(EdgeSystem.name))).scalars().all()
    edge_results = await asyncio.gather(
        *(_ping_edge(e.address) for e in edges),
        return_exceptions=True,
    )
    edge_status: list[dict[str, Any]] = []
    online_edges = 0
    for e, result in zip(edges, edge_results):
        if isinstance(result, BaseException):
            result = {"online": False, "reason": f"ping error: {result.__class__.__name__}"}
        if result.get("online"):
            online_edges += 1
        edge_status.append({
            "id": e.id, "name": e.name, "site_id": e.site_id,
            "address": e.address, **result,
        })

    # ── Storage (Neuron paths only) ──────────────────────────────────────────
    db_path = Path("data/neuron.db")
    db_bytes = db_path.stat().st_size if db_path.exists() else 0
    artifacts_path = Path(settings.build_artifacts_dir)
    artifacts_bytes = _dir_size(artifacts_path)
    mtls_dir = Path("data/mtls")
    mtls_bytes = _dir_size(mtls_dir)

    # ── Audit health ─────────────────────────────────────────────────────────
    n_audit = await session.scalar(select(func.count()).select_from(AuditEvent)) or 0
    oldest_audit = await session.scalar(
        select(AuditEvent.at).order_by(AuditEvent.at.asc()).limit(1)
    )

    # ── mTLS health ──────────────────────────────────────────────────────────
    n_certs_active = await session.scalar(
        select(func.count()).select_from(EdgeCert).where(EdgeCert.status == "active")
    ) or 0
    n_certs_total = await session.scalar(select(func.count()).select_from(EdgeCert)) or 0

    # ── Process / runtime ────────────────────────────────────────────────────
    uptime_s = round(time.monotonic() - _PROCESS_BOOT_AT, 1)

    return templates.TemplateResponse(
        "sysops.html",
        {
            "request": request,
            "now": datetime.now(timezone.utc),
            "actor_id": actor.id,
            "host": platform.node(),
            "python_version": platform.python_version(),
            "uptime_s": uptime_s,
            "uptime_human": _format_uptime(uptime_s),

            "library_count": len(catalog.by_id),
            "library_groups": {k: len(v) for k, v in catalog.by_library.items()},

            "n_roots": n_roots, "n_nodes": n_nodes, "n_edges": n_edges,
            "n_devices": n_devices, "n_releases": n_releases,

            "edges": edge_status,
            "online_edges": online_edges,
            "edge_total": len(edge_status),

            "db_path": str(db_path.resolve()), "db_size": _human_size(db_bytes),
            "artifacts_path": str(artifacts_path.resolve()),
            "artifacts_size": _human_size(artifacts_bytes),
            "mtls_path": str(mtls_dir.resolve()), "mtls_size": _human_size(mtls_bytes),

            "n_audit": n_audit,
            "audit_oldest": oldest_audit.isoformat() if oldest_audit else None,
            "audit_keep_days": getattr(settings, "audit_keep_days", 90),

            "n_certs_active": n_certs_active,
            "n_certs_total": n_certs_total,
            "ca_fingerprint": mtls.ca_fingerprint_sha256(),

            "ota_pubkey_kid": signing.signing_kid(),
            "ota_pubkey_b64": signing.public_key_b64(),

            "neuron_db_url": settings.db_url,
            "neuron_libraries_dir": str(settings.libraries_dir),
        },
    )


def _format_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)
