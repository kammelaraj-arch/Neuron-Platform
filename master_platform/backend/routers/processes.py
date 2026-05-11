from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas
from ..db import get_session
from ..models import APIKey, Process
from ..security.audit import record
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/processes", tags=["processes"])


@router.post("", response_model=schemas.ProcessOut, status_code=201)
async def create_process(
    payload: schemas.ProcessCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("processes:write")),
):
    if "nodes" not in payload.graph_json or "edges" not in payload.graph_json:
        raise HTTPException(400, "graph_json must contain 'nodes' and 'edges'")
    row = Process(
        name=payload.name,
        edge_id=payload.edge_id,
        graph_json=payload.graph_json,
        version=payload.version,
    )
    session.add(row)
    await session.flush()
    await record(session, actor=api_key.id, action="save_process",
                 target_kind="process", target_id=row.id)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("", response_model=list[schemas.ProcessOut])
async def list_processes(
    edge_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("processes:read")),
):
    stmt = select(Process).order_by(Process.updated_at.desc())
    if edge_id:
        stmt = stmt.where(Process.edge_id == edge_id)
    res = await session.execute(stmt)
    return list(res.scalars())


@router.get("/{process_id}", response_model=schemas.ProcessOut)
async def get_process(
    process_id: str,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("processes:read")),
):
    row = await session.get(Process, process_id)
    if row is None:
        raise HTTPException(404, "process not found")
    return row
