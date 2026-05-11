"""Tapo P110 / P100 smart-plug driver.

Configuration (set in the Edge's .env, reloaded on container restart):

    EDGE_TAPO_DEVICES='[
      {"device_dna":"DNA-AAAA-BBBB-CCCC-DDDD",
       "host":"192.168.1.42",
       "email":"you@example.com",
       "password":"<plug-cloud-password>"},
      ...
    ]'

Or a JSON file at ``/app/edge_runtime/data/tapo_devices.json`` with the same
shape (preferred for large fleets — keeps secrets out of env).

For each configured plug:
  * Polls every ``poll_interval_s`` seconds for power_w + state.
  * Publishes those values to ``twin/smart_plug/<dna>/reported/...`` via
    ``twin_cache.merge`` (which fans out to MQTT + Master SSE).
  * Watches the local twin cache for ``desired.switch`` changes and
    sends ``device.on()`` / ``device.off()`` accordingly.

Local-LAN only by default. The matching cloud fallback is in
``api.tapo.cloud`` (see libraries/api_library) and is intentionally
NOT used unless the operator explicitly opts in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tapo import ApiClient  # type: ignore
except ImportError:
    ApiClient = None  # type: ignore


_log = logging.getLogger("edge.integrations.tapo")


def _load_device_list() -> list[dict[str, Any]]:
    """Read EDGE_TAPO_DEVICES (JSON in env) or data/tapo_devices.json."""
    raw = os.environ.get("EDGE_TAPO_DEVICES", "").strip()
    if raw:
        try:
            return list(json.loads(raw))
        except json.JSONDecodeError as exc:
            _log.warning("EDGE_TAPO_DEVICES is not valid JSON: %s", exc)
    p = Path("data/tapo_devices.json")
    if p.exists():
        try:
            with p.open(encoding="utf-8") as fh:
                return list(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning("data/tapo_devices.json invalid: %s", exc)
    return []


class TapoDriver:
    """One driver per plug. Coordinated by ``run_loop`` below."""

    def __init__(self, *, device_dna: str, host: str, email: str, password: str,
                 poll_interval_s: float = 5.0) -> None:
        self.device_dna = device_dna
        self.host = host
        self.email = email
        self.password = password
        self.poll_interval_s = poll_interval_s
        self._client: Any = None
        self._device: Any = None
        self._desired_switch: str | None = None  # "on" / "off" / None

    async def _connect(self) -> bool:
        if ApiClient is None:
            _log.debug("tapo client missing — skipping %s", self.device_dna)
            return False
        try:
            self._client = ApiClient(self.email, self.password)
            self._device = await self._client.p110(self.host)
            return True
        except Exception as exc:  # noqa: BLE001
            _log.warning("tapo connect failed for %s @ %s: %s",
                         self.device_dna, self.host, exc)
            self._client = None
            self._device = None
            return False

    async def _read(self) -> dict[str, Any]:
        if self._device is None:
            return {"online": False}
        try:
            info = await self._device.get_device_info()
            energy = None
            try:
                energy = await self._device.get_energy_usage()
            except Exception:  # noqa: BLE001
                pass
            return {
                "online": True,
                "state": "ON" if getattr(info, "device_on", False) else "OFF",
                "power_w": getattr(energy, "current_power", 0) / 1000.0 if energy else None,
                "energy_kwh": getattr(energy, "today_energy", 0) / 1000.0 if energy else None,
                "rssi_dbm": getattr(info, "rssi", None),
                "model": getattr(info, "model", None),
            }
        except Exception as exc:  # noqa: BLE001
            _log.warning("tapo read failed for %s: %s", self.device_dna, exc)
            return {"online": False}

    async def _apply_desired(self) -> None:
        if self._device is None or self._desired_switch is None:
            return
        try:
            if self._desired_switch == "on":
                await self._device.on()
            elif self._desired_switch == "off":
                await self._device.off()
        except Exception as exc:  # noqa: BLE001
            _log.warning("tapo apply failed for %s: %s", self.device_dna, exc)

    async def tick(self, cache) -> None:
        """One iteration: pick up desired, apply, read, publish."""
        if self._device is None:
            if not await self._connect():
                # Push offline signal so the UI shows it.
                await cache.merge(
                    self.device_dna, "current",
                    {"alarm_active": True, "comm_lost_at": datetime.now(timezone.utc).isoformat()},
                )
                return
        # Pick up the desired switch from the cache (set by Master / UI).
        try:
            current = await cache.get(self.device_dna)
            self._desired_switch = (current.get("desired") or {}).get("switch")
        except Exception:  # noqa: BLE001
            pass

        await self._apply_desired()
        reported = await self._read()
        # Strip None values so we never overwrite stable fields with nulls.
        clean = {k: v for k, v in reported.items() if v is not None}
        if clean:
            await cache.merge(self.device_dna, "reported", clean)
            if reported.get("online"):
                await cache.merge(self.device_dna, "current", {"alarm_active": False})


async def run_loop(stop_event: asyncio.Event) -> None:
    """Single async task that drives every configured Tapo plug.

    Started by ``edge.main`` lifespan when EDGE_TAPO_DEVICES or
    data/tapo_devices.json is non-empty.
    """
    from ..twin_cache import cache

    device_specs = _load_device_list()
    if not device_specs:
        _log.info("Tapo integration: no devices configured — idle")
        return

    drivers: list[TapoDriver] = []
    for spec in device_specs:
        try:
            drivers.append(TapoDriver(
                device_dna=spec["device_dna"],
                host=spec["host"],
                email=spec.get("email", ""),
                password=spec.get("password", ""),
                poll_interval_s=float(spec.get("poll_interval_s", 5.0)),
            ))
        except KeyError as exc:
            _log.warning("Tapo device spec missing field %s: %r", exc, spec)

    if ApiClient is None:
        _log.warning(
            "tapo Python package not installed — driver loop is a no-op. "
            "Add 'tapo>=0.7,<2' to edge_runtime/requirements.txt to enable."
        )
        return

    _log.info("Tapo integration: driving %d plug(s)", len(drivers))
    while not stop_event.is_set():
        await asyncio.gather(
            *(d.tick(cache) for d in drivers),
            return_exceptions=True,
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
