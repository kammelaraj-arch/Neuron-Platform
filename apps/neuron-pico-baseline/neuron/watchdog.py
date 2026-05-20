"""Parent-link watchdog.

Tracks the last time the parent Pi sent a heartbeat over the local
UART. If silence exceeds `disconnect_grace_s` from the brain shell,
the watchdog calls every failsafe handler registered with
FailsafeRegistry — and they default to "motors off, alarm raised".

The watchdog has no network dependency: it relies on the UART link,
which is the most reliable channel (no WiFi/router/internet involved).
"""
import time


class ParentWatchdog:
    def __init__(self, brain, failsafe_registry):
        self.brain = brain or {}
        self.fr = failsafe_registry
        self.grace_ms = int(self.brain.get("safety", {})
                            .get("disconnect_grace_s", 5)) * 1000
        self.watchdog_ms = int(self.brain.get("safety", {})
                               .get("watchdog_ms", 1000))
        self._last_seen = self._now()
        self._tripped = False

    def _now(self):
        return time.ticks_ms() if hasattr(time, "ticks_ms") else 0

    def kick(self) -> None:
        """Call this every time we hear from the parent over UART."""
        self._last_seen = self._now()
        if self._tripped:
            self._tripped = False
            self.fr.fire("parent_link_restored")

    def tick(self) -> None:
        """Call in the main loop; trips failsafe if grace exceeded."""
        if self._tripped:
            return
        if self._now() - self._last_seen > self.grace_ms:
            self._tripped = True
            self.fr.fire("parent_link_lost",
                         silence_ms=self._now() - self._last_seen)
