"""Tests for the Edge MQTT bridge structure (no real broker)."""
from __future__ import annotations

import asyncio
import json

import pytest


def test_bridge_disabled_when_host_blank(isolated_edge):
    from edge.mqtt_bridge import configure
    assert configure("", 1883) is None
    assert configure(None, 1883) is None  # type: ignore[arg-type]


def test_bridge_configured_with_host(isolated_edge):
    from edge.mqtt_bridge import configure
    bridge = configure("mosquitto", 1883)
    assert bridge is not None
    assert bridge.host == "mosquitto"
    assert bridge.port == 1883


async def test_publish_merge_no_op_when_disconnected(isolated_edge):
    """publish_merge() must never raise even if the client isn't connected;
    device IO must not be blocked by broker availability."""
    from edge.mqtt_bridge import MQTTBridge
    bridge = MQTTBridge(host="nonexistent.example.invalid", port=1883)
    # _client is None until run() connects; publish_merge must early-return.
    await bridge.publish_merge(
        twin_kind="heater",
        device_dna="DNA-T-1-2-3-4",
        channel="reported",
        payload={"process_temp_c": 42.3},
    )


async def test_handle_twin_topic_routes_to_cache(isolated_edge):
    """Receiving a twin/<kind>/<dna>/<channel>/<field> message must merge
    into the cache."""
    from edge.mqtt_bridge import MQTTBridge
    from edge.twin_cache import TwinCache

    cache = TwinCache()
    bridge = MQTTBridge(host="x", port=1883)

    class FakeMsg:
        topic = "twin/heater/DNA-T-1-2-3-4/reported/process_temp_c"
        payload = b"42.3"

    await bridge._handle(FakeMsg, cache)
    state = await cache.get("DNA-T-1-2-3-4")
    assert state["reported"]["process_temp_c"] == 42.3


async def test_handle_invalid_topic_is_ignored(isolated_edge):
    from edge.mqtt_bridge import MQTTBridge
    from edge.twin_cache import TwinCache

    cache = TwinCache()
    bridge = MQTTBridge(host="x", port=1883)

    class FakeMsg:
        topic = "garbage/wrong/shape"
        payload = b"42"

    # Must not raise
    await bridge._handle(FakeMsg, cache)
    state = await cache.get("DNA-T-1-2-3-4")
    assert state["reported"] == {}
