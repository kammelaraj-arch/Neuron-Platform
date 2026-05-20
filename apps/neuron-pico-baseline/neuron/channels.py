"""4-channel comms manager (control, emergency, OTA, UART local).

Wires the per-Pico API key + host allow-list + cert pinning into each
of the four channels CLAUDE.md mandates. App code calls
ChannelManager.publish_telemetry / publish_emergency / poll_ota and
never touches the network primitives directly.

UART local is the parent-Pi link (GP0/GP1) — always-on regardless of
WiFi state. The other three depend on WiFi being up.
"""
import time


class ChannelManager:
    def __init__(self, api, identity, brain):
        self.api = api
        self.identity = identity
        self.brain = brain
        self._last_ota_poll = 0
        self._ota_interval_s = api.cfg["channels"]["ota"].get(
            "poll_interval_s", 3600)

    def publish_heartbeat(self) -> bool:
        return self.api.heartbeat({
            "dna": self.identity.dna,
            "uptime_ms": time.ticks_ms() if hasattr(time, "ticks_ms") else 0,
            "brain_version": self.identity.brain_version,
        })

    def publish_telemetry(self, fields: dict) -> bool:
        payload = {
            "dna": self.identity.dna,
            "ts": time.ticks_ms() if hasattr(time, "ticks_ms") else 0,
            "fields": fields,
        }
        return self.api.telemetry(payload)

    def publish_emergency(self, reason: str, detail: dict | None = None) -> bool:
        return self.api.emergency({
            "dna": self.identity.dna,
            "reason": reason,
            "detail": detail or {},
        })

    def maybe_poll_ota(self) -> dict | None:
        """Returns OTA descriptor if it's time to poll, else None."""
        now_ms = time.ticks_ms() if hasattr(time, "ticks_ms") else 0
        if now_ms - self._last_ota_poll < self._ota_interval_s * 1000:
            return None
        self._last_ota_poll = now_ms
        return self.api.ota_poll()
