"""Emergency override channel — strictly limited command set, gated by mTLS
fingerprint check via the access policy + middleware.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .command_router import bus
from .db import get_session
from .log_collector import alarms, traces


router = APIRouter(prefix="/api/emergency", tags=["emergency"])


class EmergencyBody(BaseModel):
    command: str
    device_dna: str | None = None
    note: str | None = None


_ALLOWED = {"safe_stop", "safe_shutdown", "status"}


@router.post("/command")
async def emergency_command(
    body: EmergencyBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if body.command not in _ALLOWED:
        raise HTTPException(400, f"command must be one of {sorted(_ALLOWED)}")
    fp = request.headers.get("x-client-cert-sha256", "<no-cert>")
    await traces.record(
        session,
        source="emergency",
        level="warning",
        message=f"emergency.{body.command} fingerprint={fp[:16]}…",
        device_dna=body.device_dna,
        detail={"note": body.note},
    )
    if body.command == "status":
        return {"ok": True, "command": "status"}
    if body.device_dna:
        await bus.publish(
            device_dna=body.device_dna,
            twin_kind="safety",
            channel="desired",
            field="command",
            value=body.command,
            source="emergency",
        )
    await alarms.raise_alarm(
        session,
        severity="critical",
        source=f"emergency:{fp[:16]}",
        message=f"Emergency {body.command} invoked",
        device_dna=body.device_dna,
    )
    await session.commit()
    return {"ok": True, "command": body.command}
