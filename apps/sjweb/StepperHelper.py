"""StepperHelper — Pi GPIO driver for 4× DRV8825 dual-Y XYZ plotter.

Default BCM pin map matches the CNC Shield V3 wiring:

    GPIO17 ─ X-STEP    GPIO27 ─ X-DIR
    GPIO22 ─ Y-STEP    GPIO23 ─ Y-DIR
    GPIO24 ─ Y2-STEP   GPIO25 ─ Y2-DIR    (dual-Y slave)
    GPIO5  ─ Z-STEP    GPIO6  ─ Z-DIR
    GPIO12 ─ EN  (all drivers, active LOW)

Override via constructor args. When running off-Pi (development), the
import of `RPi.GPIO` fails and the helper falls back to no-op `print`
logging — useful for testing the contour pipeline without hardware.
"""
from __future__ import annotations

import time


_DEFAULTS = {
    "x_step": 17, "x_dir": 27,
    "y_step": 22, "y_dir": 23,
    "y2_step": 24, "y2_dir": 25,
    "z_step": 5,  "z_dir": 6,
    "enable": 12,
}


class StepperHelper:
    def __init__(self, dual_y: bool = True, debug: bool = False, **overrides) -> None:
        self.dual_y = dual_y
        self.debug = debug
        self.pins = {**_DEFAULTS, **overrides}
        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for name, pin in self.pins.items():
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            # Drivers active LOW.
            GPIO.output(self.pins["enable"], GPIO.LOW)
            if debug:
                print("[StepperHelper] RPi.GPIO initialised", self.pins)
        except Exception as e:
            self._gpio = None
            if debug:
                print(f"[StepperHelper] no GPIO ({e}) — running in dry mode")

    def _set_dir(self, axis: str, value: int) -> None:
        if self._gpio is None:
            if self.debug: print(f"[dry] {axis}-DIR = {value}")
            return
        self._gpio.output(self.pins[f"{axis}_dir"], 1 if value else 0)
        if self.dual_y and axis == "y":
            self._gpio.output(self.pins["y2_dir"], 1 if value else 0)

    def _pulse(self, axis: str, delay: float) -> None:
        if self._gpio is None:
            if self.debug: print(f"[dry] {axis} step")
            return
        pin = self.pins[f"{axis}_step"]
        self._gpio.output(pin, 1)
        if self.dual_y and axis == "y":
            self._gpio.output(self.pins["y2_step"], 1)
        time.sleep(delay)
        self._gpio.output(pin, 0)
        if self.dual_y and axis == "y":
            self._gpio.output(self.pins["y2_step"], 0)
        time.sleep(delay)

    def smooth_move_interp(
        self, x_steps: int, x_dir: int, y_steps: int, y_dir: int,
        speed: int = 5,
    ) -> None:
        """Bresenham-ish XY interpolation. `speed` 1-10 → delay 0.005-0.0005s."""
        speed = max(1, min(10, int(speed)))
        delay = 0.005 - (speed - 1) * 0.0005
        if x_steps: self._set_dir("x", x_dir)
        if y_steps: self._set_dir("y", y_dir)
        if x_steps == 0 and y_steps == 0:
            return
        if x_steps >= y_steps:
            err = x_steps / 2
            for _ in range(x_steps):
                self._pulse("x", delay)
                err -= y_steps
                if err < 0:
                    self._pulse("y", delay)
                    err += x_steps
        else:
            err = y_steps / 2
            for _ in range(y_steps):
                self._pulse("y", delay)
                err -= x_steps
                if err < 0:
                    self._pulse("x", delay)
                    err += y_steps

    def jog_axis(self, axis: str, steps: int, direction: int, speed: int = 5) -> None:
        speed = max(1, min(10, int(speed)))
        delay = 0.005 - (speed - 1) * 0.0005
        self._set_dir(axis, direction)
        for _ in range(int(steps)):
            self._pulse(axis, delay)

    def cleanup(self) -> None:
        if self._gpio is None:
            return
        try:
            self._gpio.output(self.pins["enable"], self._gpio.HIGH)  # disable drivers
            self._gpio.cleanup()
        except Exception:
            pass
