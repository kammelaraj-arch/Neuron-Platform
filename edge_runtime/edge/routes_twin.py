from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .command_router import bus
from .db import get_session
from .log_collector import traces
from .models import DeviceRecord
from .twin_cache import cache


router = APIRouter(prefix="/api/v1/twin", tags=["twin"])


class CommandBody(BaseModel):
    twin_kind: str
    field: str
    value: Any
    source: str = "rest"


class ReportBody(BaseModel):
    payload: dict[str, Any]


@router.post("/{device_dna}/desired")
async def set_desired(
    device_dna: str,
    body: CommandBody,
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(DeviceRecord, device_dna)
    if row is None:
        raise HTTPException(404, "device not registered with this edge")
    state = await cache.merge(device_dna, "desired", {body.field: body.value})
    msg = await bus.publish(
        device_dna=device_dna,
        twin_kind=body.twin_kind,
        channel="desired",
        field=body.field,
        value=body.value,
        source=body.source,
    )
    await traces.record(
        session,
        source="command_router",
        message=f"desired {body.twin_kind}.{body.field}={body.value!r}",
        device_dna=device_dna,
        detail={"published": msg},
    )
    await session.commit()
    return {"state": state, "published": msg}


@router.post("/{device_dna}/reported")
async def post_reported(device_dna: str, body: ReportBody):
    state = await cache.merge(device_dna, "reported", body.payload)
    return {"state": state}


@router.post("/{device_dna}/current")
async def post_current(device_dna: str, body: ReportBody):
    state = await cache.merge(device_dna, "current", body.payload)
    return {"state": state}


@router.get("/{device_dna}")
async def get_twin(device_dna: str):
    return await cache.get(device_dna)


@router.get("")
async def list_twins():
    return await cache.all()
