"""Command router — REST endpoints that translate to MQTT topic publishes
on a local broker. When no broker is configured the router falls back to an
in-process queue consumed by the local UI / device simulators.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any


_log = logging.getLogger("edge.command_router")


class CommandBus:
    def __init__(self, max_history: int = 500) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._history: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    async def publish(self, *, device_dna: str, twin_kind: str, channel: str,
                      field: str, value: Any, source: str = "edge") -> dict[str, Any]:
        msg = {
            "topic": f"twin/{twin_kind}/{device_dna}/{channel}/{field}",
            "device_dna": device_dna,
            "twin_kind": twin_kind,
            "channel": channel,
            "field": field,
            "value": value,
            "source": source,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self._history.append(msg)
        await self._queue.put(msg)
        for sub in list(self._subscribers):
            try:
                sub.put_nowait(msg)
            except asyncio.QueueFull:
                _log.warning("subscriber queue full, dropping message")
        return msg

    def history(self, *, device_dna: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self._history)
        if device_dna:
            items = [m for m in items if m.get("device_dna") == device_dna]
        return items[-limit:]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q


bus = CommandBus()
