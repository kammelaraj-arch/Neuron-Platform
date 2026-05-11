"""Edge Tapo integration — driver structure tests (no real plug)."""
from __future__ import annotations

import asyncio
import json
import os

import pytest


def test_load_device_list_blank_env(monkeypatch, isolated_edge):
    monkeypatch.delenv("EDGE_TAPO_DEVICES", raising=False)
    from edge.integrations import tapo
    assert tapo._load_device_list() == []


def test_load_device_list_from_env(monkeypatch, isolated_edge):
    monkeypatch.setenv(
        "EDGE_TAPO_DEVICES",
        json.dumps([{"device_dna": "DNA-T-1-2-3-4", "host": "192.168.1.42",
                     "email": "x@y.z", "password": "p"}]),
    )
    from edge.integrations import tapo
    devs = tapo._load_device_list()
    assert len(devs) == 1
    assert devs[0]["device_dna"] == "DNA-T-1-2-3-4"


def test_load_device_list_from_file(tmp_path, monkeypatch, isolated_edge):
    monkeypatch.delenv("EDGE_TAPO_DEVICES", raising=False)
    work = tmp_path / "edge"
    (work / "data").mkdir(parents=True, exist_ok=True)
    (work / "data" / "tapo_devices.json").write_text(json.dumps([
        {"device_dna": "DNA-FILE-1-2-3", "host": "10.0.0.5",
         "email": "f@g.h", "password": "q"}
    ]))
    monkeypatch.chdir(work)
    from edge.integrations import tapo
    devs = tapo._load_device_list()
    assert any(d["device_dna"] == "DNA-FILE-1-2-3" for d in devs)


def test_load_device_list_bad_json(monkeypatch, isolated_edge):
    monkeypatch.setenv("EDGE_TAPO_DEVICES", "not-json")
    from edge.integrations import tapo
    assert tapo._load_device_list() == []


async def test_run_loop_idle_when_no_devices(monkeypatch, isolated_edge):
    monkeypatch.delenv("EDGE_TAPO_DEVICES", raising=False)
    from edge.integrations import tapo
    stop = asyncio.Event()
    # Should return immediately when no devices are configured.
    await asyncio.wait_for(tapo.run_loop(stop), timeout=1.0)


async def test_driver_publishes_offline_when_connect_fails(isolated_edge, monkeypatch):
    """If the tapo client can't reach the plug, the driver should publish
    a current.alarm_active=true marker so the UI shows offline."""
    from edge.integrations import tapo
    from edge.twin_cache import TwinCache
    cache = TwinCache()
    drv = tapo.TapoDriver(
        device_dna="DNA-OFFLINE-1-2-3", host="10.255.255.255",
        email="x@y.z", password="bogus", poll_interval_s=0.1,
    )

    # Force connect to always fail without needing the real package.
    async def _fail(self):
        return False
    monkeypatch.setattr(tapo.TapoDriver, "_connect", _fail)

    await drv.tick(cache)
    state = await cache.get("DNA-OFFLINE-1-2-3")
    assert state["current"].get("alarm_active") is True
