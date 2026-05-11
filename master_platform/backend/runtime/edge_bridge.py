"""Master → Edge HTTP bridge.

The Master pushes desired-state updates by calling the Edge Runtime's
``POST /api/v1/twin/{dna}/desired`` endpoint. We don't keep persistent
connections — sites are usually on intermittent links.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EdgeSystem


_log = logging.getLogger("neuron.master.edge_bridge")


class EdgeUnreachable(RuntimeError):
    pass


async def _resolve_edge_url(session: AsyncSession, edge_id: str) -> str:
    edge = await session.get(EdgeSystem, edge_id)
    if edge is None:
        raise EdgeUnreachable(f"edge_system not found: {edge_id}")
    if not edge.address:
        raise EdgeUnreachable(
            f"edge_system {edge_id} has no address configured "
            "(set address to e.g. http://192.168.10.10:8090)"
        )
    return edge.address.rstrip("/")


async def push_desired(
    session: AsyncSession,
    *,
    edge_id: str,
    device_dna: str,
    twin_kind: str,
    field: str,
    value: Any,
    timeout_s: float = 5.0,
) -> dict:
    base = await _resolve_edge_url(session, edge_id)
    url = f"{base}/api/v1/twin/{device_dna}/desired"
    body = {"twin_kind": twin_kind, "field": field, "value": value, "source": "master"}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise EdgeUnreachable(f"POST {url} failed: {exc}") from exc


async def push_many(
    session: AsyncSession,
    *,
    edge_id: str,
    device_dna: str,
    twin_kind: str,
    fields: dict[str, Any],
    timeout_s: float = 5.0,
) -> list[dict]:
    results: list[dict] = []
    for field, value in fields.items():
        try:
            results.append(
                await push_desired(
                    session,
                    edge_id=edge_id,
                    device_dna=device_dna,
                    twin_kind=twin_kind,
                    field=field,
                    value=value,
                    timeout_s=timeout_s,
                )
            )
        except EdgeUnreachable as exc:
            _log.warning("edge unreachable: %s", exc)
            results.append({"error": str(exc), "field": field})
            break
        await asyncio.sleep(0)  # cooperative
    return results
