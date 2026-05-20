"""Hardware watchdog timer.

The RP2350 has a hardware WDT that resets the chip if not "fed" within
its timeout. Used as the last-resort failsafe: if firmware locks up
inside a tight loop or a C extension hangs, the WDT triggers a clean
reset rather than leaving the device in an unknown state.

  - Default timeout: 8 s (max on RP2350)
  - Feed cadence: every main-loop iteration via .feed()

Pico apps don't need to know about this — they just keep main-loop
tick latency under the timeout. If they don't, the chip resets and
the boot loop logs WDT_RESET to the crash log.
"""

try:
    from machine import WDT
except ImportError:
    WDT = None


class HardwareWatchdog:
    def __init__(self, timeout_ms: int = 8000):
        self.timeout_ms = timeout_ms
        self._wdt = None

    def start(self) -> bool:
        if WDT is None:
            return False
        try:
            self._wdt = WDT(timeout=self.timeout_ms)
            return True
        except Exception:
            return False

    def feed(self) -> None:
        if self._wdt is not None:
            try:
                self._wdt.feed()
            except Exception:
                pass
