"""Pico-restricted REST API.

Endpoints a Pico's per-device API key (tier=pico) can call. These
endpoints are deliberately narrow — no enumeration, no mutation of
other devices, no user/audit access. The Pico's role is to report
its own state and emergencies, and to poll OTA.

Identification is by API key (X-API-Key). The key's `owner` field
encodes the device DNA as "pico:<dna>". The handlers extract that
and refuse if the body claims a different DNA.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..models import APIKey
from ..security.auth import require_scopes


_log = logging.getLogger("neuron.master.pico_api")

router = APIRouter(prefix="/api/pico", tags=["pico"])


def _claimed_dna(api_key: APIKey) -> str | None:
    # API keys for Picos have owner = "pico:<dna>"
    if not api_key.owner or not api_key.owner.startswith("pico:"):
        return None
    return api_key.owner[5:]


def _enforce_dna(api_key: APIKey, body: dict) -> str:
    claimed = _claimed_dna(api_key)
    if not claimed:
        raise HTTPException(403, "API key not bound to a Pico")
    body_dna = body.get("dna")
    if body_dna and body_dna != claimed:
        raise HTTPException(403, f"DNA mismatch: key={claimed}, body={body_dna}")
    return claimed


@router.post("/heartbeat")
async def heartbeat(
    request: Request,
    api_key: APIKey = Depends(require_scopes("heartbeat:write")),
):
    body = await request.json()
    dna = _enforce_dna(api_key, body)
    _log.info("pico heartbeat dna=%s uptime_ms=%s",
              dna, body.get("uptime_ms"))
    return {"ok": True, "dna": dna,
            "server_time": datetime.now(timezone.utc).isoformat()}


@router.post("/telemetry")
async def telemetry(
    request: Request,
    api_key: APIKey = Depends(require_scopes("telemetry:write")),
):
    body = await request.json()
    dna = _enforce_dna(api_key, body)
    fields = body.get("fields") or {}
    _log.info("pico telemetry dna=%s fields=%s", dna, list(fields.keys()))
    # TODO persist into a time-series table; for now just acknowledge.
    return {"ok": True, "dna": dna, "received": len(fields)}


@router.post("/emergency")
async def emergency(
    request: Request,
    api_key: APIKey = Depends(require_scopes("emergency:write")),
):
    body = await request.json()
    dna = _enforce_dna(api_key, body)
    reason = body.get("reason") or "unknown"
    _log.warning("pico EMERGENCY dna=%s reason=%s detail=%s",
                 dna, reason, body.get("detail"))
    # TODO push to alerting / audit / siren
    return {"ok": True, "dna": dna, "ack_reason": reason}


@router.get("/ota/poll")
async def ota_poll(
    api_key: APIKey = Depends(require_scopes("ota:read")),
) -> dict[str, Any]:
    claimed = _claimed_dna(api_key)
    # TODO consult OTA table for pending update bundles targeting this DNA.
    return {
        "dna": claimed,
        "pending": False,
        "current_base_version": "1.0.0",
    }
