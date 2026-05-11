"""In-memory twin cache (desired / reported / current) + Master-push fan-out."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import settings


_log = logging.getLogger("edge.twin_cache")


class TwinCache:
    """Per-device, per-channel JSON cache.

    Channels: ``desired``, ``reported``, ``current``. Writes are merged
    field-by-field so partial telemetry frames don't wipe stable values.
    Every successful merge fires a fire-and-forget POST to the Master's
    ``/api/v1/internal/twin-push`` endpoint so the browser SSE feed
    updates with zero added lag.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._store: dict[str, dict[str, dict[str, Any]]] = {}
        self._touched: dict[tuple[str, str], datetime] = {}

    async def merge(self, device_dna: str, channel: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            dev = self._store.setdefault(device_dna, {"desired": {}, "reported": {}, "current": {}})
            channel_state = dev.setdefault(channel, {})
            channel_state.update(payload or {})
            self._touched[(device_dna, channel)] = datetime.now(timezone.utc)
            snapshot = {k: dict(v) for k, v in dev.items()}
        # Fire push outside the lock so a slow Master can't stall device IO.
        if settings.master_push_url and settings.master_push_api_key:
            asyncio.create_task(_push_to_master(device_dna, snapshot))
        # MQTT mirror: publish each merged field under its canonical topic so
        # Pico subscribers see updates without polling. Best-effort.
        try:
            from .mqtt_bridge import bridge as _bridge
            if _bridge is not None:
                twin_kind = (device_dna.split(".")[0] if "." in device_dna else "twin")
                # Better: derive twin_kind from a registry lookup. For now
                # we publish under the channel for every twin kind topic
                # tree by prefixing 'twin'.
                asyncio.create_task(
                    _bridge.publish_merge(
                        twin_kind="twin",
                        device_dna=device_dna,
                        channel=channel,
                        payload=payload or {},
                    )
                )
        except Exception:
            pass
        return dict(channel_state)

    async def get(self, device_dna: str) -> dict[str, dict[str, Any]]:
        async with self._lock:
            dev = self._store.get(device_dna)
            if dev is None:
                return {"desired": {}, "reported": {}, "current": {}}
            return {k: dict(v) for k, v in dev.items()}

    async def all(self) -> dict[str, dict[str, dict[str, Any]]]:
        async with self._lock:
            return {dna: {k: dict(v) for k, v in chans.items()} for dna, chans in self._store.items()}

    async def last_touched(self, device_dna: str, channel: str) -> datetime | None:
        async with self._lock:
            return self._touched.get((device_dna, channel))


async def _push_to_master(device_dna: str, twin: dict[str, Any]) -> None:
    """Fire-and-forget push to the Master's /api/v1/internal/twin-push.

    Errors are logged at debug only — the local twin cache and downstream
    devices are never blocked or starved by Master-side issues.
    """
    url = settings.master_push_url.rstrip("/") + "/api/v1/internal/twin-push"
    body = {"device_dna": device_dna, "twin": twin, "edge_id": settings.edge_id}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                url,
                json=body,
                headers={"X-API-Key": settings.master_push_api_key},
            )
    except Exception as exc:  # noqa: BLE001
        _log.debug("master push failed: %s", exc)


cache = TwinCache()
