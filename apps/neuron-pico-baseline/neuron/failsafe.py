"""Failsafe registry — apps register handlers; baseline fires them.

Generic contract that any Pico app plugs into:

    fr = FailsafeRegistry()
    fr.register("estop_pressed",  lambda **_: my_app.disable_motors())
    fr.register("parent_link_lost", lambda **_: my_app.go_to_safe_value())

Then when the baseline detects the condition (E-stop ISR, parent
watchdog, OTA-poll auth failure, etc.) it calls fr.fire(name, **info)
which invokes every registered handler with the event name + context.

This is the spot CLAUDE.md's "local brain failsafe contract" lives —
the brain.json's failsafe_action per component instance is enforced
here, with no network required.
"""


class FailsafeRegistry:
    def __init__(self):
        self._handlers = {}        # name -> list[callable]
        self._fired = []           # short history for telemetry

    def register(self, name: str, handler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    def fire(self, name: str, **info) -> None:
        # Record first so handlers can call back synchronously without
        # losing their event in the history.
        self._fired.append({"name": name, "info": info})
        if len(self._fired) > 32:
            self._fired = self._fired[-32:]
        for h in self._handlers.get(name, []):
            try:
                h(**info)
            except Exception:
                # Failsafe handlers MUST NOT raise — log and continue.
                pass

    def history(self) -> list:
        return list(self._fired)
