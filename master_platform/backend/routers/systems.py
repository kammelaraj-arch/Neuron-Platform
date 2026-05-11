from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas
from ..db import get_session
from ..models import APIKey, EdgeSystem, NodeSystem, RootSystem
from ..security.audit import record
from ..security.auth import require_scopes


router = APIRouter(prefix="/api", tags=["systems"])


@router.post("/root-systems", response_model=schemas.RootSystemOut, status_code=201)
async def create_root(
    payload: schemas.RootSystemCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("systems:write")),
):
    existing = await session.scalar(select(RootSystem).where(RootSystem.name == payload.name))
    if existing:
        raise HTTPException(409, f"root system '{payload.name}' already exists")
    row = RootSystem(name=payload.name, description=payload.description)
    session.add(row)
    await session.flush()
    await record(session, actor=api_key.id, action="create_root", target_kind="root_system", target_id=row.id)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/root-systems", response_model=list[schemas.RootSystemOut])
async def list_roots(
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("systems:read")),
):
    res = await session.execute(select(RootSystem).order_by(RootSystem.created_at))
    return list(res.scalars())


@router.post("/node-systems", response_model=schemas.NodeSystemOut, status_code=201)
async def create_node(
    payload: schemas.NodeSystemCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("systems:write")),
):
    root = await session.get(RootSystem, payload.root_id)
    if root is None:
        raise HTTPException(404, "root system not found")
    row = NodeSystem(root_id=root.id, name=payload.name, region=payload.region)
    session.add(row)
    await session.flush()
    await record(session, actor=api_key.id, action="create_node", target_kind="node_system", target_id=row.id)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/node-systems", response_model=list[schemas.NodeSystemOut])
async def list_nodes(
    root_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("systems:read")),
):
    stmt = select(NodeSystem).order_by(NodeSystem.created_at)
    if root_id:
        stmt = stmt.where(NodeSystem.root_id == root_id)
    res = await session.execute(stmt)
    return list(res.scalars())


@router.post("/edge-systems", response_model=schemas.EdgeSystemOut, status_code=201)
async def create_edge(
    payload: schemas.EdgeSystemCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("systems:write")),
):
    node = await session.get(NodeSystem, payload.node_id)
    if node is None:
        raise HTTPException(404, "node system not found")
    row = EdgeSystem(
        node_id=node.id,
        name=payload.name,
        site_id=payload.site_id,
        address=payload.address,
    )
    session.add(row)
    await session.flush()
    await record(session, actor=api_key.id, action="create_edge", target_kind="edge_system", target_id=row.id)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/edge-systems", response_model=list[schemas.EdgeSystemOut])
async def list_edges(
    node_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("systems:read")),
):
    stmt = select(EdgeSystem).order_by(EdgeSystem.created_at)
    if node_id:
        stmt = stmt.where(EdgeSystem.node_id == node_id)
    res = await session.execute(stmt)
    return list(res.scalars())
