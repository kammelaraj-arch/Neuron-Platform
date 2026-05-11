"""mTLS endpoints — issue, list, revoke Edge client certs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, EdgeCert, EdgeSystem
from ..security import mtls
from ..security.audit import record
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/mtls", tags=["mtls"])


class IssueBody(BaseModel):
    subject_cn: str
    edge_id: str | None = None
    validity_days: int = 365
    sans_dns: list[str] | None = None
    notes: str | None = None


class IssuedCertOut(BaseModel):
    id: str
    subject_cn: str
    fingerprint_sha256: str
    serial: str
    issued_at: str
    expires_at: str
    cert_pem: str
    key_pem: str
    note: str = "key_pem is shown ONCE — store it securely; it is NOT persisted server-side."


@router.get("/ca")
async def get_ca(_: APIKey = Depends(require_scopes("library:read"))) -> dict:
    """Return the platform CA cert so Edge runtimes can validate certs the
    Master gives them. Public-key material; safe to expose to any holder
    of a read-scope API key.
    """
    return {
        "ca_cert_pem": mtls.ca_cert_pem(),
        "ca_fingerprint_sha256": mtls.ca_fingerprint_sha256(),
    }


@router.get("/ca.pem", response_class=PlainTextResponse)
async def get_ca_pem(_: APIKey = Depends(require_scopes("library:read"))) -> str:
    """CA cert as a downloadable PEM blob."""
    return mtls.ca_cert_pem()


@router.post("/edge-certs", response_model=IssuedCertOut, status_code=201)
async def issue_edge_cert(
    body: IssueBody,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("admin")),
):
    if body.edge_id is not None:
        edge = await session.get(EdgeSystem, body.edge_id)
        if edge is None:
            raise HTTPException(404, f"edge_system {body.edge_id} not found")
    issued = mtls.issue_edge_cert(
        subject_cn=body.subject_cn,
        validity_days=max(1, min(int(body.validity_days), 3650)),
        sans_dns=body.sans_dns or [],
    )
    row = EdgeCert(
        edge_id=body.edge_id,
        subject_cn=issued["subject_cn"],
        fingerprint_sha256=issued["fingerprint_sha256"],
        serial=issued["serial"],
        issued_at=__import__("datetime").datetime.fromisoformat(issued["issued_at"]),
        expires_at=__import__("datetime").datetime.fromisoformat(issued["expires_at"]),
        issued_by=actor.id,
        status="active",
        notes=body.notes,
    )
    session.add(row)
    await session.flush()
    await record(
        session, actor=actor.id, action="issue_edge_cert",
        target_kind="edge_cert", target_id=row.id,
        detail={"subject_cn": body.subject_cn, "edge_id": body.edge_id,
                "fingerprint_sha256": issued["fingerprint_sha256"]},
    )
    await session.commit()
    return IssuedCertOut(
        id=row.id,
        subject_cn=issued["subject_cn"],
        fingerprint_sha256=issued["fingerprint_sha256"],
        serial=issued["serial"],
        issued_at=issued["issued_at"],
        expires_at=issued["expires_at"],
        cert_pem=issued["cert_pem"],
        key_pem=issued["key_pem"],
    )


@router.get("/edge-certs")
async def list_edge_certs(
    edge_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("admin")),
):
    stmt = select(EdgeCert).order_by(EdgeCert.issued_at.desc())
    if edge_id:
        stmt = stmt.where(EdgeCert.edge_id == edge_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "certs": [
            {
                "id": r.id, "edge_id": r.edge_id, "subject_cn": r.subject_cn,
                "fingerprint_sha256": r.fingerprint_sha256, "serial": r.serial,
                "issued_at": r.issued_at.isoformat() if r.issued_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "status": r.status, "issued_by": r.issued_by,
            }
            for r in rows
        ]
    }


@router.post("/edge-certs/{cert_id}/revoke", status_code=204)
async def revoke_edge_cert(
    cert_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("admin")),
):
    row = await session.get(EdgeCert, cert_id)
    if row is None:
        raise HTTPException(404, "edge cert not found")
    row.status = "revoked"
    await record(
        session, actor=actor.id, action="revoke_edge_cert",
        target_kind="edge_cert", target_id=cert_id,
        detail={"fingerprint_sha256": row.fingerprint_sha256},
    )
    await session.commit()
    return None
