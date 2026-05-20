"""Failsafe handler registry.

Apps register named handlers; the baseline fires them on:

  parent_link_lost      — UART silence > grace
  parent_link_restored  — UART came back
  estop_pressed         — E-stop ISR
  estop_released        — mushroom button cleared (does NOT auto-rearm)
  wifi_lost             — WLAN disconnected
  wifi_restored         — WLAN reconnected
  integrity_failure     — config HMAC or app-manifest mismatch
  brownout_detected     — last reset cause was brownout

Handlers MUST NOT raise (any exception is swallowed and logged) and
SHOULD be idempotent. Per CLAUDE.md the local brain is the last line
of defence — the failsafe is what actually disables the heater pin,
hopefully before the substance catches fire.
"""


class FailsafeRegistry:
    def __init__(self, crash_log=None):
        self._handlers = {}
        self._fired = []
        self._crash_log = crash_log

    def register(self, name: str, handler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    def fire(self, name: str, **info) -> None:
        self._fired.append({"name": name, "info": info})
        if len(self._fired) > 64:
            self._fired = self._fired[-64:]
        for h in self._handlers.get(name, []):
            try:
                h(**info)
            except Exception as e:
                if self._crash_log:
                    self._crash_log.record(
                        "failsafe_handler_error",
                        info={"event": name, "handler": str(h)},
                        exc=e,
                    )

    def history(self) -> list:
        return list(self._fired)
