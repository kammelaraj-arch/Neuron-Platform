"""4-channel comms manager: control + emergency + OTA + UART local.

App code calls ChannelManager.publish_telemetry / publish_emergency /
poll_ota / publish_heartbeat. It never touches the network primitives
directly — those go through NeuronAPI which enforces allow-list + pin.

UART (local) is the parent-Pi link; it's managed separately by the
app's UART loop. ChannelManager just exposes a hook for the watchdog
to know when the UART last saw traffic.
"""
import time

def _now_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else 0


class ChannelManager:
    def __init__(self, api, identity, brain):
        self.api = api
        self.identity = identity
        self.brain = brain or {}
        self._last_ota_poll = 0
        ch = api.cfg["channels"]
        self._ota_interval_s = ch["ota"].get("poll_interval_s", 3600)
        self._hb_interval_ms = int(ch.get("control", {}).get("heartbeat_interval_s", 30)) * 1000
        self._last_hb = 0

    # ── heartbeat ──────────────────────────────────────────────────
    def maybe_heartbeat(self, extra=None) -> bool:
        now = _now_ms()
        if now - self._last_hb < self._hb_interval_ms:
            return False
        self._last_hb = now
        return self.publish_heartbeat(extra)

    def publish_heartbeat(self, extra=None) -> bool:
        try:
            import gc
            mem_free = gc.mem_free()
        except Exception:
            mem_free = 0
        payload = {
            "dna":           self.identity.dna,
            "uptime_ms":     _now_ms(),
            "brain_version": self.identity.brain_version,
            "app_version":   self.identity.app_version,
            "mem_free":      mem_free,
            "last_reset":    self.identity.last_reset_cause,
        }
        if extra:
            payload.update(extra)
        return self.api.heartbeat(payload)

    # ── telemetry ──────────────────────────────────────────────────
    def publish_telemetry(self, fields):
        return self.api.telemetry({
            "dna":    self.identity.dna,
            "ts":     _now_ms(),
            "fields": fields,
        })

    # ── emergency ──────────────────────────────────────────────────
    def publish_emergency(self, reason, detail=None):
        return self.api.emergency({
            "dna":    self.identity.dna,
            "reason": reason,
            "detail": detail or {},
            "ts":     _now_ms(),
        })

    # ── OTA ────────────────────────────────────────────────────────
    def maybe_poll_ota(self):
        now = _now_ms()
        if now - self._last_ota_poll < self._ota_interval_s * 1000:
            return None
        self._last_ota_poll = now
        return self.api.ota_poll()
