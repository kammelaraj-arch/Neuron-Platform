"""Bidirectional heartbeat between child and parent.

Child → parent: publish a small JSON every `child_to_parent_ms` to
    `heartbeat/<device_dna>` with cpu / memory / uptime / sequence
    number. The parent reads these to decide if we're online.

Parent → child: subscribe to `heartbeat/<device_dna>/down` and reset
    `_last_parent_seen` on every receive. The agent's main loop reads
    `seconds_since_parent_heartbeat()` and flips the SafetyEngine into
    autonomous mode when it crosses the grace threshold.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable


log = logging.getLogger("neuron_agent.heartbeat")


def _proc_stat() -> dict:
    """Cheap, dependency-free uptime + 1-min load. Works on every Linux
    + falls back to zeros elsewhere so the dev host can run the agent."""
    try:
        uptime = float(open("/proc/uptime").read().split()[0])
    except OSError:
        uptime = 0.0
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        load = 0.0
    try:
        meminfo = open("/proc/meminfo").read().splitlines()
        total = avail = 0
        for line in meminfo:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
        mem_used_pct = (1.0 - avail / total) * 100.0 if total else 0.0
    except OSError:
        mem_used_pct = 0.0
    return {"uptime_s": int(uptime), "load_1": load,
            "mem_used_pct": round(mem_used_pct, 1)}


class HeartbeatLoop:
    def __init__(
        self,
        device_dna: str,
        interval_ms: int,
        publish: Callable[[str, dict], None],
        subscribe: Callable[[str, Callable[[bytes], None]], None] | None = None,
    ) -> None:
        self.device_dna = device_dna
        self.interval = max(0.05, interval_ms / 1000.0)
        self.publish = publish
        self.subscribe = subscribe
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self._last_parent_seen = time.monotonic()

    def start(self) -> None:
        if self.subscribe is not None:
            self.subscribe(f"heartbeat/{self.device_dna}/down", self._on_parent_heartbeat)
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="neuron-heartbeat")
        self._thread.start()
        log.info("heartbeat started — interval=%.2fs dna=%s",
                 self.interval, self.device_dna)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def seconds_since_parent_heartbeat(self) -> float:
        return time.monotonic() - self._last_parent_seen

    def _on_parent_heartbeat(self, payload: bytes) -> None:
        self._last_parent_seen = time.monotonic()
        log.debug("parent heartbeat (%d bytes)", len(payload))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._seq += 1
            msg = {
                "dna": self.device_dna,
                "seq": self._seq,
                "ts": int(time.time()),
                **_proc_stat(),
            }
            try:
                self.publish(f"heartbeat/{self.device_dna}", msg)
            except Exception as e:
                log.warning("heartbeat publish failed: %s", e)
            self._stop.wait(self.interval)
