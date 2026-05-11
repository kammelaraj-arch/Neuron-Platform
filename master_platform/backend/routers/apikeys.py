from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas
from ..db import get_session
from ..models import APIKey
from ..security.audit import record
from ..security.auth import require_scopes
from ..security.keys import issue_payload


router = APIRouter(prefix="/api/apikeys", tags=["apikeys"])


@router.post("", response_model=schemas.APIKeyIssued, status_code=201)
async def create_key(
    payload: schemas.APIKeyCreate,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("apikeys:write")),
):
    secret, kw = issue_payload(
        label=payload.label,
        owner=payload.owner,
        tier=payload.tier,
        scopes=payload.scopes,
        rate_per_minute=payload.rate_per_minute,
        rate_burst=payload.rate_burst,
        ttl_days=payload.ttl_days,
    )
    row = APIKey(**kw)
    session.add(row)
    await session.flush()
    await record(session, actor=actor.id, action="create_apikey",
                 target_kind="apikey", target_id=row.id,
                 detail={"owner": row.owner, "tier": row.tier})
    await session.commit()
    await session.refresh(row)
    return {"record": row, "secret": secret}


@router.get("", response_model=list[schemas.APIKeyOut])
async def list_keys(
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("apikeys:read")),
):
    res = await session.execute(select(APIKey).order_by(APIKey.issued_at.desc()))
    return list(res.scalars())


@router.post("/{key_id}/rotate", response_model=schemas.APIKeyIssued)
async def rotate_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("apikeys:write")),
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
    await record(session, actor=actor.id, action="rotate_apikey",
                 target_kind="apikey", target_id=new.id,
                 detail={"rotated_from": old.id})
    await session.commit()
    await session.refresh(new)
    return {"record": new, "secret": secret}


@router.post("/{key_id}/revoke", status_code=204)
async def revoke_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("apikeys:write")),
):
    row = await session.get(APIKey, key_id)
    if row is None:
        raise HTTPException(404, "API key not found")
    row.status = "revoked"
    await record(session, actor=actor.id, action="revoke_apikey",
                 target_kind="apikey", target_id=row.id)
    await session.commit()
    return None
