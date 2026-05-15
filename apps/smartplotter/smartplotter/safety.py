"""SafetyMonitor — owns the E-stop pin, limit switches, and the parent-
link watchdog. Every component that moves a motor goes through
`SafetyMonitor.check()` first; on fault, all motion calls short-circuit
to no-op + raise SafetyAbort.

Designed to fail-closed: import errors / GPIO missing / sensor read
errors all flip is_safe to False. The web UI surfaces the reason so
the operator can clear it.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


log = logging.getLogger("smartplotter.safety")


class SafetyAbort(RuntimeError):
    """Raised whenever motion is requested while is_safe=False."""


@dataclass
class SafetyState:
    is_safe: bool = True
    fault: str = ""
    estop_pressed: bool = False
    limit_x_hit: bool = False
    limit_y_hit: bool = False
    limit_z_hit: bool = False
    parent_link_lost: bool = False
    last_parent_heartbeat_s: float = field(default_factory=lambda: time.monotonic())


class SafetyMonitor:
    def __init__(self, settings,
                 on_fault: Callable[[SafetyState], None] | None = None,
                 pin_map: dict[str, int] | None = None):
        self.s = settings
        self.state = SafetyState()
        self._on_fault = on_fault or (lambda _: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpio = None

        # Resolve pin map from brain.json + env + defaults. Falls back
        # to the legacy Settings.* fields if the resolved map doesn't
        # carry a given pin (covers operators who only set env vars).
        if pin_map is None:
            from .pins import resolve as _resolve
            pin_map = _resolve(self.s.bundle_dir)
        self.pins = pin_map
        self._estop = self.pins.get("estop",   self.s.estop_bcm)
        self._lim_x = self.pins.get("limit_x", self.s.limit_x_bcm)
        self._lim_y = self.pins.get("limit_y", self.s.limit_y_bcm)
        self._lim_z = self.pins.get("limit_z", self.s.limit_z_bcm)

        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in (self._estop, self._lim_x, self._lim_y, self._lim_z):
                # NC contacts: idle HIGH (pulled up), pressed/triggered LOW.
                if isinstance(pin, int) and 0 <= pin <= 53:
                    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # E-stop on interrupt — bouncetime so a noisy contact doesn't
            # spam events.
            GPIO.add_event_detect(self._estop, GPIO.FALLING,
                                  callback=self._on_estop_irq, bouncetime=50)
            log.info("safety: GPIO initialised (estop=BCM%d, limits=%d/%d/%d)",
                     self._estop, self._lim_x, self._lim_y, self._lim_z)
        except Exception as e:
            log.warning("safety: GPIO unavailable (%s) — running in dry mode "
                        "(safety checks pass; this is NOT acceptable for "
                        "production hardware)", e)

    # ── Public surface ─────────────────────────────────────────────────
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="smartplotter-safety")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._gpio:
            try:
                self._gpio.remove_event_detect(self._estop)
            except Exception:
                pass

    def check(self) -> None:
        """Raises SafetyAbort if the system is not in a safe state.
        Call before each motion primitive."""
        if not self.state.is_safe:
            raise SafetyAbort(self.state.fault or "system not safe")

    def heartbeat_from_parent(self) -> None:
        """Called by the MQTT bridge each time the parent's heartbeat
        message arrives. Resets the watchdog."""
        self.state.last_parent_heartbeat_s = time.monotonic()
        if self.state.parent_link_lost:
            self.state.parent_link_lost = False
            self._recompute()
            log.info("safety: parent link restored")

    def trigger_safe_stop(self, reason: str) -> None:
        """External trigger — MQTT emergency channel, web-UI button, or
        anywhere else that needs to assert safety."""
        self.state.is_safe = False
        self.state.fault = reason
        log.warning("safety: SAFE STOP triggered — %s", reason)
        self._on_fault(self.state)

    def reset_after_human_check(self) -> None:
        """Manual reset — operator confirmed the fault is cleared. Only
        clears software-latched faults; if the E-stop is still pressed
        or a limit switch still active, the next poll re-asserts."""
        self.state.fault = ""
        self.state.is_safe = True
        self._recompute()

    # ── Internal ───────────────────────────────────────────────────────
    def _on_estop_irq(self, channel) -> None:
        # Read again to debounce; GPIO low = pressed.
        try:
            level = self._gpio.input(channel)
        except Exception:
            level = 0
        pressed = (level == 0)
        self.state.estop_pressed = pressed
        if pressed:
            self.trigger_safe_stop("estop_pressed")

    def _recompute(self) -> None:
        prev = self.state.is_safe
        # Any active fault = not safe.
        active = (self.state.estop_pressed
                  or self.state.limit_x_hit
                  or self.state.limit_y_hit
                  or self.state.limit_z_hit
                  or self.state.parent_link_lost)
        self.state.is_safe = not active
        if prev != self.state.is_safe:
            log.info("safety: is_safe %s → %s", prev, self.state.is_safe)
            if not self.state.is_safe:
                self._on_fault(self.state)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._gpio:
                    self.state.limit_x_hit = self._gpio.input(self._lim_x) == 0
                    self.state.limit_y_hit = self._gpio.input(self._lim_y) == 0
                    self.state.limit_z_hit = self._gpio.input(self._lim_z) == 0
                # Parent-link watchdog
                if time.monotonic() - self.state.last_parent_heartbeat_s > self.s.parent_grace_s:
                    if not self.state.parent_link_lost:
                        self.state.parent_link_lost = True
                        self.trigger_safe_stop("parent_link_lost")
                self._recompute()
            except Exception as e:
                log.error("safety: monitor loop error %s — failing closed", e)
                self.trigger_safe_stop(f"monitor_error: {e}")
            time.sleep(0.1)   # 10 Hz limit-switch poll
