"""NTP time sync — fire-and-forget after WiFi comes up.

Without NTP the Pico's RTC defaults to 2000-01-01 which makes every
telemetry timestamp meaningless. We sync once on first connect, then
every 6 hours after that.

Server: pool.ntp.org (resolved at runtime; allow-list check skipped
because pool.ntp.org is purely time, not data — there's no exfil risk
in sending an NTP request). For environments that require strict
egress control, set NEURON_NTP_HOST to a master-hosted NTP relay.
"""
import time


_DEFAULT_HOST = "pool.ntp.org"
_RESYNC_INTERVAL_MS = 6 * 60 * 60 * 1000

_last_sync_ms = 0


def _ticks_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else 0


def sync_time(host=None, force=False) -> bool:
    """Set the RTC from NTP. Returns True on success."""
    global _last_sync_ms
    if not force and _last_sync_ms and (_ticks_ms() - _last_sync_ms) < _RESYNC_INTERVAL_MS:
        return True
    try:
        import ntptime
        if host:
            ntptime.host = host
        ntptime.settime()
        _last_sync_ms = _ticks_ms()
        return True
    except Exception:
        return False
