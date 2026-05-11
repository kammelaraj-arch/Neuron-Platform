from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, AuditEvent
from ..security.audit import record
from ..security.keys import issue_payload
from ..security.ui_auth import (
    consume_last_secret,
    stash_last_secret,
    ui_require_admin,
    ui_require_login,
)


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-secrets"])


_ALLOWED_TIERS = ("admin", "integration", "device", "readonly")


@router.get("/ui/secrets", response_class=HTMLResponse)
async def secrets_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    rows = (
        await session.execute(
            select(APIKey).order_by(APIKey.issued_at.desc())
        )
    ).scalars().all()
    last_secret = consume_last_secret(request)
    return templates.TemplateResponse(
        "secrets.html",
        {
            "request": request,
            "rows": rows,
            "last_secret": last_secret,
            "current_key_id": actor.id,
            "tiers": _ALLOWED_TIERS,
        },
    )


@router.post("/ui/secrets/new")
async def secrets_create(
    request: Request,
    label: str = Form(""),
    owner: str = Form(...),
    tier: str = Form("integration"),
    scopes: str = Form(""),
    rate_per_minute: int = Form(120),
    rate_burst: int = Form(30),
    ttl_days: str = Form("365"),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if tier not in _ALLOWED_TIERS:
        raise HTTPException(400, f"invalid tier: {tier}")
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    ttl = int(ttl_days) if ttl_days.strip().isdigit() and int(ttl_days) > 0 else None
    secret, kw = issue_payload(
        label=label or None,
        owner=owner,
        tier=tier,
        scopes=scope_list,
        rate_per_minute=max(1, rate_per_minute),
        rate_burst=max(1, rate_burst),
        ttl_days=ttl,
    )
    row = APIKey(**kw)
    session.add(row)
    await session.flush()
    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action="create_apikey",
        target_kind="apikey",
        target_id=row.id,
        detail={"owner": row.owner, "tier": row.tier, "scopes": scope_list},
    )
    await session.commit()
    stash_last_secret(request, secret)
    return RedirectResponse("/ui/secrets", status_code=303)


@router.post("/ui/secrets/{key_id}/rotate")
async def secrets_rotate(
    key_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    old = await session.get(APIKey, key_id)
    if old is None or old.status == "revoked":
        raise HTTPException(404, "API key not found")
    secret, kw = issue_payload(
        label=old.label,
        owner=old.owner,
        tier=old.tier,
        scopes=list(old.scopes_json or []),
        rate_per_minute=old.rate_per_minute,
        rate_burst=old.rate_burst,
        ttl_days=365,
    )
    new = APIKey(**kw, rotated_from=old.id)
    session.add(new)
    old.status = "rotated"
    await session.flush()
    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action="rotate_apikey",
        target_kind="apikey",
        target_id=new.id,
        detail={"rotated_from": old.id},
    )
    await session.commit()
    stash_last_secret(request, secret)
    return RedirectResponse("/ui/secrets", status_code=303)


@router.post("/ui/secrets/{key_id}/revoke")
async def secrets_revoke(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if key_id == actor.id:
        raise HTTPException(400, "refusing to revoke the key you are signed in with")
    row = await session.get(APIKey, key_id)
    if row is None:
        raise HTTPException(404, "API key not found")
    row.status = "revoked"
    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action="revoke_apikey",
        target_kind="apikey",
        target_id=row.id,
    )
    await session.commit()
    return RedirectResponse("/ui/secrets", status_code=303)


@router.get("/ui/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    actor_filter: str | None = None,
    action_filter: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(ui_require_login),
):
    stmt = select(AuditEvent).order_by(AuditEvent.at.desc()).limit(500)
    if actor_filter:
        stmt = stmt.where(AuditEvent.actor == actor_filter)
    if action_filter:
        stmt = stmt.where(AuditEvent.action == action_filter)
    rows = (await session.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        "audit.html",
        {
            "request": request,
            "rows": rows,
            "actor_filter": actor_filter or "",
            "action_filter": action_filter or "",
        },
    )


# ─── mTLS — Edge cert issuance UI (admin) ───────────────────────────────────
@router.get("/ui/mtls", response_class=HTMLResponse)
async def mtls_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(ui_require_admin),
):
    from ..models import EdgeCert, EdgeSystem
    from ..security import mtls
    rows = (await session.execute(
        select(EdgeCert).order_by(EdgeCert.issued_at.desc())
    )).scalars().all()
    edges = (await session.execute(select(EdgeSystem).order_by(EdgeSystem.name))).scalars().all()

    last = request.session.pop("mtls_last_issued", None)
    return templates.TemplateResponse(
        "mtls.html",
        {
            "request": request,
            "rows": rows,
            "edges": edges,
            "ca_pem": mtls.ca_cert_pem(),
            "ca_fingerprint": mtls.ca_fingerprint_sha256(),
            "last_issued": last,
        },
    )


@router.post("/ui/mtls/new")
async def mtls_issue(
    request: Request,
    subject_cn: str = Form(...),
    edge_id: str = Form(""),
    validity_days: int = Form(365),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..models import EdgeCert, EdgeSystem
    from ..security import mtls
    from ..security.audit import record
    import datetime

    if edge_id and (await session.get(EdgeSystem, edge_id)) is None:
        edge_id = ""

    issued = mtls.issue_edge_cert(
        subject_cn=subject_cn.strip(),
        validity_days=max(1, min(int(validity_days), 3650)),
    )
    row = EdgeCert(
        edge_id=edge_id or None,
        subject_cn=issued["subject_cn"],
        fingerprint_sha256=issued["fingerprint_sha256"],
        serial=issued["serial"],
        issued_at=datetime.datetime.fromisoformat(issued["issued_at"]),
        expires_at=datetime.datetime.fromisoformat(issued["expires_at"]),
        issued_by=actor.id,
        status="active",
        notes=notes or None,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="issue_edge_cert", target_kind="edge_cert", target_id=row.id,
        detail={"subject_cn": subject_cn, "edge_id": edge_id or None,
                "fingerprint_sha256": issued["fingerprint_sha256"]},
    )
    await session.commit()

    # Stash plaintext cert+key for ONE-shot render. Same pattern as the
    # bootstrap API key flow.
    request.session["mtls_last_issued"] = {
        "id": row.id,
        "subject_cn": issued["subject_cn"],
        "fingerprint_sha256": issued["fingerprint_sha256"],
        "cert_pem": issued["cert_pem"],
        "key_pem": issued["key_pem"],
        "expires_at": issued["expires_at"],
    }
    return RedirectResponse("/ui/mtls", status_code=303)


@router.post("/ui/mtls/{cert_id}/revoke")
async def mtls_revoke(
    cert_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..models import EdgeCert
    from ..security.audit import record
    row = await session.get(EdgeCert, cert_id)
    if row is None:
        return RedirectResponse("/ui/mtls", status_code=303)
    row.status = "revoked"
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="revoke_edge_cert", target_kind="edge_cert", target_id=cert_id,
        detail={"fingerprint_sha256": row.fingerprint_sha256},
    )
    await session.commit()
    return RedirectResponse("/ui/mtls", status_code=303)
