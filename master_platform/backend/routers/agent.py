"""Pull-agent endpoints for remote devices behind NAT.

The Pi (or future Edge / Pico) opens **no inbound ports**. Its only
network interaction is outbound polling against this Master, which
keeps the local-brain failsafe contract (CLAUDE.md) intact:

    POST /api/agent/{device_id}/poll
        Bearer <agent_token>
        → 200 [{id, kind, payload}, ...]    pending commands, now 'running'

    POST /api/agent/{device_id}/report
        Bearer <agent_token>
        {command_id, status: 'done'|'failed', result?, error?}
        → 204

This mirrors the OTA poll model: child decides when to talk, parent
stages work, latency = poll interval (default 3 s — operator-tunable
per device for power-vs-snappiness trade-offs).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import DeviceCommand, PlotterDevice
from ..security.audit import record

_log = logging.getLogger("neuron.agent")
router = APIRouter(prefix="/api/agent", tags=["agent"])


async def _stamp_hw_identity(device: PlotterDevice, hw_kind: str | None,
                             hw_id: str | None) -> bool:
    """Capture the Pi's CPU serial / MAC on first poll. If a different
    physical Pi tries to reuse the same agent token, we refuse — the
    operator should rotate the token rather than allow silent device
    swapping (which would orphan group memberships)."""
    if not (hw_kind and hw_id):
        return False
    if device.hw_id and device.hw_id == hw_id:
        return False        # stable, no-op
    if device.hw_id and device.hw_id != hw_id:
        raise HTTPException(
            409,
            f"agent token already bound to a different physical device "
            f"({device.hw_kind}:{device.hw_id[:12]}…). Rotate the token "
            f"on /ui/plotter if this is intentional."
        )
    device.hw_id = hw_id
    device.hw_kind = hw_kind
    return True


async def _resolve_device(session: AsyncSession, device_id: str,
                          authorization: str) -> PlotterDevice:
    """Validate bearer token belongs to this device. We look up by
    (id, agent_token) atomically so a leaked token can't impersonate
    a different device."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "missing bearer token")
    device = await session.get(PlotterDevice, device_id)
    if device is None or device.agent_token != token:
        # 401 — don't distinguish "wrong device" from "wrong token" to
        # prevent enumeration of device ids by an attacker with a token.
        raise HTTPException(401, "invalid bearer token for this device")
    if device.status != "active":
        raise HTTPException(403, f"device status={device.status!r}")
    return device


@router.post("/{device_id}/poll", response_class=JSONResponse)
async def agent_poll(
    device_id: str,
    request: Request,
    authorization: str = Header(""),
    x_neuron_hw_kind: str = Header(""),
    x_neuron_hw_id: str = Header(""),
    session: AsyncSession = Depends(get_session),
):
    device = await _resolve_device(session, device_id, authorization)
    await _stamp_hw_identity(device, x_neuron_hw_kind or None, x_neuron_hw_id or None)
    now = datetime.now(timezone.utc)
    device.last_seen_at = now

    # Pull every pending command for this device and mark them running
    # in one transaction. The agent is responsible for reporting back —
    # if it dies mid-execution, the command sits in 'running' forever
    # and the operator can re-queue manually. (A later GC sweep can
    # auto-fail running commands older than N minutes.)
    pending = (await session.execute(
        select(DeviceCommand)
        .where(DeviceCommand.device_id == device_id,
               DeviceCommand.status == "pending")
        .order_by(DeviceCommand.created_at)
    )).scalars().all()
    out = []
    for cmd in pending:
        cmd.status = "running"
        cmd.started_at = now
        out.append({
            "id": cmd.id,
            "kind": cmd.kind,
            "payload": cmd.payload_json or {},
        })
    await session.commit()
    return {"commands": out, "poll_interval_s": device.agent_poll_interval_s}


@router.post("/{device_id}/report")
async def agent_report(
    device_id: str,
    request: Request,
    authorization: str = Header(""),
    session: AsyncSession = Depends(get_session),
):
    device = await _resolve_device(session, device_id, authorization)
    body = await request.json()
    cid = (body or {}).get("command_id") or (body or {}).get("id")
    status = (body or {}).get("status", "").lower()
    if not cid or status not in ("done", "failed"):
        raise HTTPException(400, "command_id + status (done|failed) required")
    cmd = await session.get(DeviceCommand, cid)
    if cmd is None or cmd.device_id != device_id:
        raise HTTPException(404, "command not found for this device")
    cmd.status = status
    cmd.result_json = (body.get("result") or {})
    cmd.error = (body.get("error") or None)
    cmd.completed_at = datetime.now(timezone.utc)
    device.last_seen_at = cmd.completed_at
    await record(
        session, actor=device_id, actor_kind="agent",
        action=f"agent.report.{status}", target_kind="device_command",
        target_id=cid,
        detail={"kind": cmd.kind, "error": cmd.error},
    )
    await session.commit()
    return Response(status_code=204)


async def enqueue(session: AsyncSession, *, device_id: str, device_kind: str,
                  kind: str, payload: dict) -> DeviceCommand:
    """Enqueue a command for the agent's next poll. Used by Plotter
    Activate (UI + Alexa SceneController) and any future Master-side
    code that needs to drive a NAT'd child."""
    cmd = DeviceCommand(
        device_id=device_id,
        device_kind=device_kind,
        kind=kind,
        payload_json=payload or {},
    )
    session.add(cmd)
    await session.flush()
    return cmd
