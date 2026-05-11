"""Thin GPIO wrappers around ``machine.Pin`` so the rest of the firmware never
touches the platform module directly. Falls back to a stub when run on
CPython for unit tests.
"""
try:
    from machine import Pin  # type: ignore
except ImportError:
    Pin = None


class _StubPin:
    OUT = "OUT"
    IN = "IN"
    PULL_UP = "UP"
    PULL_DOWN = "DOWN"

    def __init__(self, *_args, **_kwargs):
        self._v = 0

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = 1 if v else 0


def output(pin, *, active="high"):
    p = Pin(pin, Pin.OUT) if Pin else _StubPin()
    p.value(0 if active == "high" else 1)
    return p


def input_pin(pin, *, pull="none"):
    if Pin:
        pull_arg = None
        if pull == "up":
            pull_arg = Pin.PULL_UP
        elif pull == "down":
            pull_arg = Pin.PULL_DOWN
        return Pin(pin, Pin.IN, pull_arg)
    return _StubPin()


def write(p, level):
    p.value(1 if level else 0)


def read(p):
    return p.value()
