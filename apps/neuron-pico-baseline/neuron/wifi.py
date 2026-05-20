"""WiFi connection manager with auto-reconnect and exponential backoff.

Brings WiFi up on demand and silently reconnects when the access point
drops, the router restarts, or the chip's WLAN driver wedges. App code
calls `manager.ensure_connected()` periodically; the manager handles
state internally.

Backoff: 1s → 2s → 5s → 15s → 60s (caps at 60s). Resets on success.
The LED reflects state: connected = STATE_OK, disconnected = STATE_DEGRADED.
"""
import time

try:
    import network
except ImportError:
    network = None


_BACKOFF_SCHEDULE_MS = (1000, 2000, 5000, 15000, 60000)


def _ticks_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else 0


class WiFiManager:
    def __init__(self, wifi_cfg):
        self.cfg = wifi_cfg or {}
        self.wlan = None
        self._backoff_idx = 0
        self._next_attempt_ms = 0
        self._last_state = None
        if network is not None:
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
            cc = self.cfg.get("country_code")
            if cc:
                try:
                    network.country(cc)
                except Exception:
                    pass

    @property
    def connected(self) -> bool:
        return bool(self.wlan and self.wlan.isconnected())

    @property
    def ifconfig(self):
        try:
            return self.wlan.ifconfig() if self.wlan else None
        except Exception:
            return None

    def ensure_connected(self) -> bool:
        """Call from the main loop. Returns True if connected."""
        if self.wlan is None or not self.cfg.get("ssid"):
            return False
        if self.connected:
            self._backoff_idx = 0
            return True
        now = _ticks_ms()
        if now < self._next_attempt_ms:
            return False
        # Time to attempt (re)connect.
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        try:
            self.wlan.connect(self.cfg["ssid"], self.cfg.get("password") or "")
        except Exception:
            pass
        # Give it up to ~3 seconds — short so we don't block the main loop.
        for _ in range(30):
            if self.connected:
                self._backoff_idx = 0
                return True
            time.sleep_ms(100) if hasattr(time, "sleep_ms") else time.sleep(0.1)
        # Failed — schedule next attempt with backoff.
        wait = _BACKOFF_SCHEDULE_MS[min(self._backoff_idx, len(_BACKOFF_SCHEDULE_MS) - 1)]
        self._next_attempt_ms = _ticks_ms() + wait
        if self._backoff_idx < len(_BACKOFF_SCHEDULE_MS) - 1:
            self._backoff_idx += 1
        return False
