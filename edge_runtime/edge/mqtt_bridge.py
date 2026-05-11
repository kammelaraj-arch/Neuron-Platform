"""MQTT bridge — connects the Edge's twin cache + command bus to a local
Mosquitto broker.

Behaviour:
  * Subscribes to twin/+/+/+/+ and bridges every published message into
    twin_cache.merge() so MQTT clients (Pico, dashboards) and HTTP
    clients (Master) see the same canonical state.
  * Subscribes to device/+/heartbeat for auto-registry updates.
  * On every cache merge (from any source), publishes the new value
    back to MQTT so Pico subscribers get instant updates without
    polling.
  * Strictly OPTIONAL — if the broker isn't reachable, the Edge keeps
    serving REST + SSE traffic unchanged.

Topic shape:
    twin/<twin_kind>/<device_dna>/{desired,reported,current}/<field>
    device/<device_dna>/heartbeat
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

try:
    from asyncio_mqtt import Client, MqttError  # type: ignore
except ImportError:
    try:
        from aiomqtt import Client, MqttError  # type: ignore
    except ImportError:
        Client = None  # type: ignore
        MqttError = Exception  # type: ignore


_log = logging.getLogger("edge.mqtt_bridge")


class MQTTBridge:
    """Async MQTT subscriber + publisher tied to the Edge's twin_cache."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        client_id: str = "neuron-edge",
        keepalive: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.keepalive = keepalive
        self._stop = asyncio.Event()
        self._client: Any = None

    async def publish_merge(self, *, twin_kind: str, device_dna: str,
                             channel: str, payload: dict[str, Any]) -> None:
        """Publish each field under its canonical topic. Called by twin_cache
        on every merge."""
        if self._client is None:
            return
        try:
            for field, value in (payload or {}).items():
                topic = f"twin/{twin_kind}/{device_dna}/{channel}/{field}"
                await self._client.publish(topic, json.dumps(value), retain=True)
        except MqttError as exc:
            _log.debug("mqtt publish failed: %s", exc)

    async def run(self) -> None:
        """Main loop. Reconnects on disconnect with exponential backoff."""
        if Client is None:
            _log.warning("aiomqtt/asyncio_mqtt not installed — MQTT bridge disabled")
            return

        from .twin_cache import cache

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with Client(
                    hostname=self.host,
                    port=self.port,
                    client_id=self.client_id,
                    keepalive=self.keepalive,
                ) as client:
                    self._client = client
                    backoff = 1.0
                    _log.info("mqtt connected to %s:%s", self.host, self.port)
                    async with client.messages() as messages:
                        await client.subscribe("twin/+/+/+/+")
                        await client.subscribe("device/+/heartbeat")
                        async for msg in messages:
                            try:
                                await self._handle(msg, cache)
                            except Exception as exc:  # noqa: BLE001
                                _log.warning("mqtt handler error: %s", exc)
                            if self._stop.is_set():
                                break
            except MqttError as exc:
                _log.warning("mqtt connection lost: %s — retrying in %.1fs", exc, backoff)
                self._client = None
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def _handle(self, msg, cache) -> None:
        topic = str(msg.topic)
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload.decode("utf-8", errors="replace")

        parts = topic.split("/")
        # twin/<kind>/<dna>/<channel>/<field>
        if len(parts) == 5 and parts[0] == "twin":
            _, twin_kind, device_dna, channel, field = parts
            if channel in ("desired", "reported", "current"):
                await cache.merge(device_dna, channel, {field: payload})
            return

        # device/<dna>/heartbeat
        if len(parts) == 3 and parts[0] == "device" and parts[2] == "heartbeat":
            _log.info("mqtt heartbeat: %s", parts[1])
            return

    def stop(self) -> None:
        self._stop.set()


bridge: MQTTBridge | None = None


def configure(host: str | None, port: int) -> MQTTBridge | None:
    global bridge
    if not host:
        bridge = None
        return None
    bridge = MQTTBridge(host=host, port=port)
    return bridge
