"""Parent-link (UART) watchdog.

Tracks the last time the parent Pi spoke to us. If silence exceeds the
brain's disconnect_grace_s, the watchdog fires `parent_link_lost` on
the FailsafeRegistry — which by contract means apps disable motors,
enforce per-component failsafe actions, and announce upstream.

This is independent of WiFi state. The UART link to the parent is the
most reliable channel (no DNS, no routing, no internet) so a UART loss
is the strongest possible "we are alone" signal.
"""
import time


def _now_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else 0


class ParentWatchdog:
    def __init__(self, brain, failsafe_registry):
        self.brain = brain or {}
        self.fr = failsafe_registry
        safety = self.brain.get("safety") or {}
        self.grace_ms     = int(safety.get("disconnect_grace_s", 5)) * 1000
        self.watchdog_ms  = int(safety.get("watchdog_ms", 1000))
        self._last_seen   = _now_ms()
        self._tripped     = False

    def kick(self) -> None:
        """Call every time we hear from the parent over UART."""
        self._last_seen = _now_ms()
        if self._tripped:
            self._tripped = False
            self.fr.fire("parent_link_restored")

    def tick(self) -> None:
        """Call in the main loop; trips failsafe once on grace exceed."""
        if self._tripped:
            return
        elapsed = _now_ms() - self._last_seen
        if elapsed > self.grace_ms:
            self._tripped = True
            self.fr.fire("parent_link_lost", silence_ms=elapsed)

    @property
    def silence_ms(self) -> int:
        return _now_ms() - self._last_seen

    @property
    def tripped(self) -> bool:
        return self._tripped
