"""Push-driven twin telemetry hub.

End-to-end path (zero polling on the hot path):

  Pico  ──HTTP POST──>  Edge twin cache  ──HTTP POST──>  Master /push  ──SSE──>  Browser

Each hop fires the moment data arrives. Total end-to-end latency
is bounded by the network RTT, not by any polling interval.

A slow fallback poll runs ONLY when no Edge push has been seen for the
device in a while AND there are active subscribers; this lets the page
detect a silent Edge going dark.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Device, EdgeSystem


_log = logging.getLogger("neuron.master.twin_stream")

# We never poll while pushes are arriving normally. If no push for this
# many seconds and there are subscribers, fall back to a single Edge
# poll every FALLBACK_INTERVAL_S so the UI surfaces an offline state.
_FALLBACK_AFTER_S = 8
_FALLBACK_INTERVAL_S = 5
_HEARTBEAT_S = 15


def _format(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


class _DeviceFan:
    def __init__(self, device_dna: str) -> None:
        self.device_dna = device_dna
        self._subs: list[asyncio.Queue[str]] = []
        self._last: dict | None = None
        self._last_push_at: float = 0.0
        self._fallback_task: asyncio.Task | None = None

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subs)

    def add(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._subs.append(q)
        if self._last is not None:
            try:
                q.put_nowait(_format(self._last))
            except asyncio.QueueFull:
                pass
        # Start a fallback monitor if not already running.
        if self._fallback_task is None or self._fallback_task.done():
            self._fallback_task = asyncio.create_task(self._fallback_loop())
        return q

    def remove(self, q: asyncio.Queue[str]) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    def push(self, payload: dict[str, Any]) -> None:
        """Fire from the request thread of /api/v1/internal/twin-push."""
        self._last = payload
        self._last_push_at = time.monotonic()
        msg = _format(payload)
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                _log.warning("subscriber queue full for %s", self.device_dna)

    async def _poll_edge_once(self) -> dict[str, Any]:
        async with SessionLocal() as session:
            device = await session.get(Device, self.device_dna)
            if device is None:
                return {"online": False, "reason": "device_unknown"}
            edge = await session.get(EdgeSystem, device.edge_id)
            if edge is None or not edge.address:
                return {"online": False, "reason": "edge_address_unset"}
            url = f"{edge.address.rstrip('/')}/api/v1/twin/{self.device_dna}"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return {"online": False, "reason": f"edge HTTP {resp.status_code}"}
            return {
                "online": True,
                "twin": resp.json(),
                "at": datetime.now(timezone.utc).isoformat(),
                "source": "fallback_poll",
            }
        except httpx.HTTPError as exc:
            return {"online": False, "reason": f"edge unreachable ({exc.__class__.__name__})"}
        except Exception as exc:  # noqa: BLE001
            return {"online": False, "reason": f"poll error ({exc.__class__.__name__})"}

    async def _fallback_loop(self) -> None:
        """Wakes if no push for _FALLBACK_AFTER_S; otherwise stays idle."""
        try:
            while self._subs:
                await asyncio.sleep(_FALLBACK_INTERVAL_S)
                if not self._subs:
                    return
                quiet_for = time.monotonic() - self._last_push_at
                if self._last_push_at and quiet_for < _FALLBACK_AFTER_S:
                    continue
                try:
                    payload = await self._poll_edge_once()
                except Exception as exc:  # noqa: BLE001
                    payload = {"online": False, "reason": f"fallback errored ({exc.__class__.__name__})"}
                if payload != self._last:
                    self._last = payload
                    msg = _format(payload)
                    for q in list(self._subs):
                        try:
                            q.put_nowait(msg)
                        except asyncio.QueueFull:
                            pass
        except asyncio.CancelledError:
            pass


class TwinStreamHub:
    def __init__(self) -> None:
        self._fans: dict[str, _DeviceFan] = {}
        self._lock = asyncio.Lock()

    def push(self, device_dna: str, payload: dict[str, Any]) -> None:
        fan = self._fans.get(device_dna)
        if fan is None:
            fan = _DeviceFan(device_dna)
            self._fans[device_dna] = fan
        fan.push(payload)

    async def subscribe(self, device_dna: str) -> AsyncIterator[str]:
        async with self._lock:
            fan = self._fans.get(device_dna)
            if fan is None:
                fan = _DeviceFan(device_dna)
                self._fans[device_dna] = fan
            q = fan.add()
        try:
            yield ": ok\n\n"  # flip EventSource to OPEN
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_S)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            fan.remove(q)


hub = TwinStreamHub()
