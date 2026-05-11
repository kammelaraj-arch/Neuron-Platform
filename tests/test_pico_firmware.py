"""Pico firmware modules — exercise under CPython with stubbed micropython
imports.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


def _stub_micropython_modules():
    for m in ("machine", "network", "urequests", "onewire", "ds18x20", "umqtt", "umqtt.simple"):
        sys.modules.setdefault(m, types.ModuleType("stub_" + m.replace(".", "_")))


def test_dna_loader_handles_missing_file(isolated_master):
    _stub_micropython_modules()
    sys.modules.pop("device_dna", None)
    from device_dna import DNA

    d = DNA(path="/dev/null")
    assert d.device_dna == "DNA-UNPROV"
    assert d.device_type == "pico2w"


def test_offline_queue_drops_oldest_on_overflow():
    sys.modules.pop("offline_mode", None)
    from offline_mode import OfflineQueue

    q = OfflineQueue(buffer_size=4)
    for i in range(7):
        q.enqueue({"i": i})
    # Newest 4 retained.
    assert len(q) == 4
    sent_ids: list[int] = []
    drained = q.flush(lambda p: (sent_ids.append(p["i"]) or True))
    assert drained == 4
    assert sent_ids == [3, 4, 5, 6]
    assert len(q) == 0


def test_offline_queue_flush_stops_on_first_failure():
    sys.modules.pop("offline_mode", None)
    from offline_mode import OfflineQueue

    q = OfflineQueue(buffer_size=10)
    for i in range(5):
        q.enqueue({"i": i})

    sent: list[int] = []

    def sender(payload):
        sent.append(payload["i"])
        return payload["i"] < 2  # fail at i=2

    drained = q.flush(sender)
    assert drained == 2
    assert sent == [0, 1, 2]
    # Three remain, in original order.
    rest: list[int] = []
    q.flush(lambda p: (rest.append(p["i"]) or True))
    assert rest == [2, 3, 4]


def test_brain_overtemp_forces_safe_state(tmp_path):
    _stub_micropython_modules()
    sys.modules.pop("brain", None)

    cfg_path = tmp_path / "brain.json"
    cfg_path.write_text(
        '{"device_dna":"DNA-X","brain_version":"1.0.0","config_schema_version":"1.0.0",'
        '"loops":[{"id":"h","type":"pid","period_ms":50,"inputs":[],"outputs":[],'
        '"params":{"kp":2.0,"ki":0.0,"kd":0.0,"out_min":0,"out_max":100}}],'
        '"safety":{"safe_state":{"ssr_duty":0},'
        '"interlocks":[{"id":"overtemp","condition":"process_temp_c > safety_max_c",'
        '"action":"go_safe","alarm_severity":"critical"}],'
        '"watchdog_ms":1000},'
        '"telemetry":{"publish_period_ms":1000,"topics":[{"topic":"x","fields":["y"]}]},'
        '"offline_policy":{"mode":"continue_safe","max_offline_seconds":60,"buffer_size":4}}'
    )
    from brain import Brain

    b = Brain(str(cfg_path))
    b.apply_desired({"setpoint_c": 80, "safety_max_c": 100})
    b.update_inputs({"process_temp_c": 25.0})
    out_normal = b.step()
    assert out_normal.get("ssr_duty") == 100  # kp*err = 2*55 → clamped to out_max

    # Inject over-temp → safe state, alarm flag.
    b.update_inputs({"process_temp_c": 150.0})
    out_safe = b.step()
    assert out_safe == {"ssr_duty": 0}
    assert b.snapshot()["current"]["alarm_active"] is True


def test_logger_respects_min_level(capsys):
    sys.modules.pop("logger", None)
    from logger import info, warn, set_level

    set_level("warning")
    info("src", "should be filtered out")
    warn("src", "should appear")
    captured = capsys.readouterr()
    assert "should be filtered out" not in captured.out
    assert "should appear" in captured.out
