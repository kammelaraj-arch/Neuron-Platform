"""Thin GPIO abstraction. On a real Pi we use gpiozero (which itself
wraps RPi.GPIO / lgpio underneath); on a dev host we fall back to a
mock backend that just logs what would have happened. The agent doesn't
care which backend it gets — only the brain's interlocks do, and they
treat both the same."""
from __future__ import annotations

import logging
from typing import Protocol


log = logging.getLogger("neuron_agent.gpio")


class GpioBackend(Protocol):
    def set(self, pin: str, value: int | float) -> None: ...
    def read(self, pin: str) -> int | float: ...
    def close(self) -> None: ...


class MockBackend:
    """Dev-host backend — logs everything. State is purely in memory so
    tests can read back what the agent has done."""
    def __init__(self) -> None:
        self.state: dict[str, int | float] = {}

    def set(self, pin: str, value: int | float) -> None:
        self.state[pin] = value
        log.info("MOCK set %s=%s", pin, value)

    def read(self, pin: str) -> int | float:
        v = self.state.get(pin, 0)
        log.debug("MOCK read %s -> %s", pin, v)
        return v

    def close(self) -> None:
        log.info("MOCK close")
        self.state.clear()


class GpioZeroBackend:
    """Real-Pi backend. Lazy-imports gpiozero so the dev host doesn't
    need it installed. Each pin is opened once and cached."""
    def __init__(self) -> None:
        import gpiozero  # noqa: F401  (raises on non-Pi if it can't find a factory)
        self._pins: dict[str, "object"] = {}

    def _resolve_bcm(self, pin: str) -> int:
        # Accept "GPIO17", "17", "BCM17", "GP4" (Pico-style). Strip
        # everything but digits and use that as BCM number.
        digits = "".join(ch for ch in pin if ch.isdigit())
        if not digits:
            raise ValueError(f"can't resolve BCM number for pin {pin!r}")
        return int(digits)

    def _output(self, pin: str):
        from gpiozero import OutputDevice
        bcm = self._resolve_bcm(pin)
        if pin not in self._pins:
            self._pins[pin] = OutputDevice(bcm, active_high=True, initial_value=False)
        return self._pins[pin]

    def set(self, pin: str, value: int | float) -> None:
        dev = self._output(pin)
        if value:
            dev.on()
        else:
            dev.off()

    def read(self, pin: str) -> int | float:
        from gpiozero import InputDevice
        bcm = self._resolve_bcm(pin)
        if pin not in self._pins:
            self._pins[pin] = InputDevice(bcm)
        return 1 if self._pins[pin].is_active else 0

    def close(self) -> None:
        for p in self._pins.values():
            try:
                p.close()
            except Exception:
                pass
        self._pins.clear()


def open_backend(mock: bool = False) -> GpioBackend:
    """Resolve the right backend. mock=True forces the dev-host backend
    even if gpiozero happens to be importable."""
    if mock:
        log.info("GPIO backend = mock (forced)")
        return MockBackend()
    try:
        b = GpioZeroBackend()
        log.info("GPIO backend = gpiozero (real Pi)")
        return b
    except Exception as e:
        log.warning("gpiozero unavailable (%s); falling back to mock", e)
        return MockBackend()
