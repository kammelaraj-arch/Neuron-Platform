"""End-to-end SSE push pipeline test (no real network)."""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
async def hub(isolated_master):
    from backend.runtime.twin_stream import TwinStreamHub
    return TwinStreamHub()


async def _read_one(gen, timeout=2.0):
    return await asyncio.wait_for(gen.__anext__(), timeout=timeout)


async def test_push_delivers_to_open_subscriber(hub):
    sub = hub.subscribe("DNA-T-1-2-3-4")

    # Initial ': ok' chunk arrives immediately.
    first = await _read_one(sub)
    assert first.startswith(": ok")

    # Push from the "edge".
    hub.push("DNA-T-1-2-3-4", {
        "online": True, "twin": {"desired": {"setpoint_c": 80}, "reported": {}, "current": {}},
        "at": "2026-05-09T12:34:56Z", "source": "edge_push",
    })
    chunk = await _read_one(sub)
    assert chunk.startswith("data: ")
    payload = json.loads(chunk.split("\n", 1)[0][6:])
    assert payload["twin"]["desired"]["setpoint_c"] == 80
    assert payload["source"] == "edge_push"
    await sub.aclose()


async def test_late_subscriber_gets_cached_last_value(hub):
    hub.push("DNA-T-9-9-9-9", {
        "online": True, "twin": {"desired": {"a": 1}, "reported": {}, "current": {}},
    })
    sub = hub.subscribe("DNA-T-9-9-9-9")
    # First chunk = ': ok', second = the cached state.
    first = await _read_one(sub)
    assert first.startswith(": ok")
    cached = await _read_one(sub)
    payload = json.loads(cached.split("\n", 1)[0][6:])
    assert payload["twin"]["desired"]["a"] == 1
    await sub.aclose()


async def test_multiple_subscribers_each_get_pushes(hub):
    sub_a = hub.subscribe("DNA-T-FAN-OUT")
    sub_b = hub.subscribe("DNA-T-FAN-OUT")
    await _read_one(sub_a)  # ': ok'
    await _read_one(sub_b)  # ': ok'

    hub.push("DNA-T-FAN-OUT", {"online": True, "twin": {"desired": {"x": 7}, "reported": {}, "current": {}}})

    a = await _read_one(sub_a)
    b = await _read_one(sub_b)
    assert "\"x\":7" in a
    assert "\"x\":7" in b

    await sub_a.aclose(); await sub_b.aclose()
