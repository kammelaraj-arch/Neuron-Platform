"""Status LED state codes.

Visible-from-across-the-room diagnostic without needing a USB cable:

  STATE_BOOT      : fast blink     — bootstrap in progress
  STATE_OK        : solid on       — everything healthy
  STATE_DEGRADED  : slow blink     — WiFi down, UART-only mode
  STATE_FAILSAFE  : double-blink   — parent link lost / motors off
  STATE_FAULT     : SOS pattern    — boot failure / integrity error

Pi 5 parent should also display these in its UI by reading the latest
heartbeat — but the LED is the always-works fallback.
"""
import time


STATE_BOOT     = "boot"
STATE_OK       = "ok"
STATE_DEGRADED = "degraded"
STATE_FAILSAFE = "failsafe"
STATE_FAULT    = "fault"


# (on_ms, off_ms, repeat_count_or_-1_for_infinite)
_PATTERNS = {
    STATE_BOOT:     [(100, 100)],
    STATE_OK:       [(1, 0)],          # solid on (effectively)
    STATE_DEGRADED: [(800, 800)],
    STATE_FAILSAFE: [(150, 150), (150, 600)],
    STATE_FAULT:    [(200, 200), (200, 200), (200, 600),
                     (600, 200), (600, 200), (600, 600),
                     (200, 200), (200, 200), (200, 1500)],
}


class StatusLED:
    def __init__(self):
        self._pin = None
        try:
            from machine import Pin
            self._pin = Pin("LED", Pin.OUT)
        except Exception:
            try:
                from machine import Pin
                self._pin = Pin(25, Pin.OUT)   # legacy Pico (RP2040)
            except Exception:
                self._pin = None
        self._state = STATE_BOOT
        self._pattern_idx = 0
        self._next_change_ms = 0
        self._on = False

    def set(self, state: str) -> None:
        if state not in _PATTERNS:
            return
        if state == self._state:
            return
        self._state = state
        self._pattern_idx = 0
        self._next_change_ms = 0
        self._on = False
        self._set_pin(False)

    def tick(self) -> None:
        """Call frequently from the main loop. Drives the blink pattern."""
        if self._pin is None:
            return
        now = time.ticks_ms() if hasattr(time, "ticks_ms") else 0
        if now < self._next_change_ms:
            return
        pattern = _PATTERNS[self._state]
        step = pattern[self._pattern_idx]
        on_ms, off_ms = step[0], step[1]
        if not self._on:
            self._set_pin(True)
            self._next_change_ms = now + on_ms
            self._on = True
        else:
            self._set_pin(False)
            self._next_change_ms = now + off_ms
            self._on = False
            self._pattern_idx = (self._pattern_idx + 1) % len(pattern)

    def _set_pin(self, on: bool) -> None:
        try:
            self._pin.value(1 if on else 0)
        except Exception:
            pass
