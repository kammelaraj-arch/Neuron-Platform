"""MotionPlanner — trapezoidal-acceleration motion for the 4× DRV8825
dual-Y XYZ plotter.

Every motion call:
    1. Asks SafetyMonitor.check() — raises SafetyAbort if unsafe.
    2. Bounds-checks the target against the soft envelope from Settings.
    3. Computes a trapezoidal velocity profile (accel → cruise → decel).
    4. Drives steps with the computed per-step delay.

Soft limits enforced HERE; hard limits (limit switches) are watched by
SafetyMonitor and cause SafetyAbort mid-motion.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Iterable

from .safety import SafetyAbort, SafetyMonitor
from .pins import resolve as resolve_pins


log = logging.getLogger("smartplotter.motion")


class MotionPlanner:
    def __init__(self, settings, safety: SafetyMonitor, dual_y: bool = True,
                 pin_map: dict[str, int] | None = None,
                 position_callback=None):
        self.s = settings
        self.safety = safety
        self.dual_y = dual_y
        # Pin map resolved from brain.json + env + defaults. Override
        # via the constructor when testing.
        self.pins = pin_map or resolve_pins(settings.bundle_dir)
        # Internal step accumulator. Reset to (0,0,0) after homing.
        self.x = 0
        self.y = 0
        self.z = 0
        # Live-position publisher — called every ~25 ms (40 Hz) during
        # motion with (x_mm, y_mm, z_mm, pen_down). Used by the web UI's
        # digital-twin canvas. Decoupled via callback so motion.py
        # doesn't import socketio.
        self.position_callback = position_callback
        self._last_pos_emit_s = 0.0
        self._gpio = None
        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in self.pins.values():
                # Skip negative / out-of-range entries (safety guards).
                if isinstance(pin, int) and 0 <= pin <= 53:
                    try:
                        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                    except Exception as e:
                        log.warning("motion: skip pin %s (%s)", pin, e)
            self.drivers_enable(True)
            log.info("motion: GPIO ready (dual_y=%s) — using pins %s",
                     dual_y, self.pins)
        except Exception as e:
            log.warning("motion: GPIO unavailable (%s) — dry mode", e)

    # ── Driver enable (active LOW on DRV8825) ─────────────────────────
    def drivers_enable(self, enabled: bool) -> None:
        if self._gpio is None:
            return
        self._gpio.output(self.pins["enable"], 0 if enabled else 1)

    # ── Soft-limit-checked move ───────────────────────────────────────
    def move_to_mm(self, x_mm: float | None = None,
                   y_mm: float | None = None,
                   z_mm: float | None = None,
                   feed_mm_s: float | None = None) -> None:
        """Move to an absolute mm target. Pass None to skip an axis."""
        self.safety.check()
        feed = min(feed_mm_s or self.s.max_feed_mm_s, self.s.max_feed_mm_s)
        # Soft-limit + steps target
        tx = self._clamp(x_mm, 0, self.s.max_x_mm) if x_mm is not None else self.x / self.s.steps_per_mm
        ty = self._clamp(y_mm, 0, self.s.max_y_mm) if y_mm is not None else self.y / self.s.steps_per_mm
        tz = self._clamp(z_mm, 0, self.s.max_z_mm) if z_mm is not None else self.z / self.s.steps_per_mm
        target_x = int(round(tx * self.s.steps_per_mm))
        target_y = int(round(ty * self.s.steps_per_mm))
        target_z = int(round(tz * self.s.steps_per_mm))
        self._move_to_steps(target_x, target_y, target_z, feed)

    def move_relative_mm(self, dx_mm: float = 0, dy_mm: float = 0, dz_mm: float = 0,
                         feed_mm_s: float | None = None) -> None:
        cx = self.x / self.s.steps_per_mm
        cy = self.y / self.s.steps_per_mm
        cz = self.z / self.s.steps_per_mm
        self.move_to_mm(cx + dx_mm, cy + dy_mm, cz + dz_mm, feed_mm_s)

    # ── Internal: trapezoidal step-rate driver ────────────────────────
    def _move_to_steps(self, tx: int, ty: int, tz: int, feed_mm_s: float) -> None:
        dx = tx - self.x
        dy = ty - self.y
        dz = tz - self.z
        if dx == 0 and dy == 0 and dz == 0:
            return
        x_dir = 1 if dx > 0 else 0
        y_dir = 1 if dy > 0 else 0
        z_dir = 1 if dz > 0 else 0
        adx, ady, adz = abs(dx), abs(dy), abs(dz)
        total_steps = max(adx, ady, adz)

        # Convert feed to step-rate, then to a trapezoidal velocity
        # profile (accel-ramp + cruise + decel-ramp). Z usually moves
        # alone but the math is the same.
        max_rate = feed_mm_s * self.s.steps_per_mm
        accel_rate = self.s.accel_mm_s2 * self.s.steps_per_mm
        accel_steps = min(total_steps // 2,
                          int((max_rate ** 2) / (2 * accel_rate)) if accel_rate > 0 else total_steps)
        decel_steps = accel_steps
        cruise_steps = max(0, total_steps - accel_steps - decel_steps)

        # Set direction pins once (DRV8825 latches on first step pulse).
        if self._gpio is not None:
            if adx: self._set_dir("x", x_dir)
            if ady: self._set_dir("y", y_dir)
            if adz: self._set_dir("z", z_dir)

        # Bresenham-style: combine X / Y / Z into one stepping loop so
        # all three move concurrently. Z usually does step-then-cruise
        # before XY, but for a plotter we pull-up / pull-down once at
        # the start/end of each contour so concurrent is fine.
        ex = ady // 2
        ey = adz // 2
        cur_x = cur_y = cur_z = 0
        for i in range(total_steps):
            self.safety.check()
            # Per-step delay from the velocity profile.
            if i < accel_steps:
                v = math.sqrt(2 * accel_rate * (i + 1))
            elif i < accel_steps + cruise_steps:
                v = max_rate
            else:
                remaining = total_steps - i
                v = math.sqrt(2 * accel_rate * remaining)
            v = max(50.0, min(v, max_rate))   # floor: 50 steps/s
            half_period = 0.5 / v

            if cur_x < adx:
                self._pulse("x", half_period)
                cur_x += 1
            ex += ady
            if ex >= adx:
                if cur_y < ady:
                    self._pulse("y", half_period)
                    cur_y += 1
                ex -= adx
            ey += adz
            if ey >= adx:
                if cur_z < adz:
                    self._pulse("z", half_period)
                    cur_z += 1
                ey -= adx

            # Live position pulse to the web UI — throttled to ~40 Hz.
            if self.position_callback is not None:
                now = time.monotonic()
                if now - self._last_pos_emit_s >= 0.025:
                    cx = (self.x + (cur_x if x_dir else -cur_x)) / self.s.steps_per_mm
                    cy = (self.y + (cur_y if y_dir else -cur_y)) / self.s.steps_per_mm
                    cz = (self.z + (cur_z if z_dir else -cur_z)) / self.s.steps_per_mm
                    try:
                        self.position_callback(cx, cy, cz,
                                               pen_down=(cz <= 0.5))
                    except Exception:
                        pass
                    self._last_pos_emit_s = now

        self.x = tx
        self.y = ty
        self.z = tz
        # Final position emit so the twin always lands exactly on the target.
        if self.position_callback is not None:
            try:
                self.position_callback(self.x / self.s.steps_per_mm,
                                       self.y / self.s.steps_per_mm,
                                       self.z / self.s.steps_per_mm,
                                       pen_down=(self.z / self.s.steps_per_mm <= 0.5))
            except Exception:
                pass

    # ── Pulse / dir helpers ───────────────────────────────────────────
    def _set_dir(self, axis: str, value: int) -> None:
        if self._gpio is None:
            return
        self._gpio.output(self.pins[f"{axis}_dir"], 1 if value else 0)
        if self.dual_y and axis == "y":
            self._gpio.output(self.pins["y2_dir"], 1 if value else 0)

    def _pulse(self, axis: str, half_period_s: float) -> None:
        if self._gpio is None:
            return
        pin = self.pins[f"{axis}_step"]
        self._gpio.output(pin, 1)
        if self.dual_y and axis == "y":
            self._gpio.output(self.pins["y2_step"], 1)
        time.sleep(half_period_s)
        self._gpio.output(pin, 0)
        if self.dual_y and axis == "y":
            self._gpio.output(self.pins["y2_step"], 0)
        time.sleep(half_period_s)

    def cleanup(self) -> None:
        if self._gpio is None:
            return
        try:
            self.drivers_enable(False)
            self._gpio.cleanup()
        except Exception:
            pass

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(v, hi))


# Convenience entry for the contour runner.
def run_segments(planner: MotionPlanner,
                 segments: Iterable[tuple[float, float]],
                 feed_mm_s: float,
                 z_lift_mm: float = 2.0,
                 progress_cb=lambda i, n: None) -> None:
    """Trace a sequence of XY points. Pen lifts at the start (z up),
    drops on first segment (z down), pen up at the end."""
    pts = list(segments)
    if not pts:
        return
    # Pen up, fast-travel to start, pen down.
    planner.move_to_mm(z_mm=z_lift_mm)
    planner.move_to_mm(x_mm=pts[0][0], y_mm=pts[0][1],
                       feed_mm_s=planner.s.max_feed_mm_s)
    planner.move_to_mm(z_mm=0)
    for i, (x, y) in enumerate(pts):
        planner.move_to_mm(x_mm=x, y_mm=y, feed_mm_s=feed_mm_s)
        if i % 25 == 0:
            progress_cb(i, len(pts))
    planner.move_to_mm(z_mm=z_lift_mm)
    progress_cb(len(pts), len(pts))
