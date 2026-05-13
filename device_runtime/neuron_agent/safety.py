"""Safety enforcement — the local Brain.

The brain.json bundle ships two things we evaluate here:

1. `safety.interlocks[]` — derived by the master from each
   ComponentInstance's risk_level / failsafe_action. Trigger:
   parent_link_lost. When the parent heartbeat hasn't been seen for
   the component's grace_seconds, this code drives the failsafe.

2. `safety.rules[]` — operator-authored IF→THEN rules from the wizard
   ("Temp > 80 → stop_motor"). Evaluated continuously against the
   latest telemetry. Critical rules fire LOCALLY regardless of parent
   state — the parent can't veto a critical rule. Non-critical rules
   only fire when the parent is alive AND has acknowledged.

Failsafe action vocabulary (matches master models.FAILSAFE_ACTIONS +
RULE_ACTIONS):
    off                 GPIO low
    hold_last           do nothing — keep current state
    go_to_safe_value    GPIO PWM = failsafe_value
    alarm_only          publish_alarm topic, no actuator change
    stop                same as `off` but emits an alarm too
    fail_open           GPIO high (for fail-open relays)
    fail_closed         GPIO low (for fail-closed relays)
    stop_motor          alias for stop (rules-side action)
    set_pwm_zero        alias for off
    open_relay          fail_open
    close_relay         fail_closed
    publish_alarm       alarm_only
    safe_shutdown       full systemd shutdown (last resort)
    ignore              no-op (rules-side action; for muting)
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from .gpio import GpioBackend


log = logging.getLogger("neuron_agent.safety")

# Map every alias → canonical pin-level effect.
_PIN_EFFECT = {
    "off":              ("low", None),
    "hold_last":        ("none", None),
    "go_to_safe_value": ("value", "failsafe"),
    "alarm_only":       ("none", None),
    "stop":             ("low", "alarm"),
    "fail_open":        ("high", None),
    "fail_closed":      ("low", None),
    "stop_motor":       ("low", "alarm"),
    "set_pwm_zero":     ("low", None),
    "open_relay":       ("high", None),
    "close_relay":      ("low", None),
    "publish_alarm":    ("none", "alarm"),
    "safe_shutdown":    ("low", "shutdown"),
    "ignore":           ("none", None),
}


class SafetyEngine:
    """Single point that owns interlock state + rule evaluation.

    Owns no I/O loop of its own; the agent's main loop ticks it via
    `evaluate()` after each telemetry sample, and calls
    `enforce_parent_link_lost()` exactly once when the parent heartbeat
    goes silent.
    """

    def __init__(
        self,
        brain: dict,
        gpio: GpioBackend,
        publish_alarm: Callable[[dict], None],
        shutdown: Callable[[], None] = lambda: None,
    ) -> None:
        self.brain = brain
        self.gpio = gpio
        self.publish_alarm = publish_alarm
        self.shutdown = shutdown
        self._interlocks_fired: set[str] = set()
        self._rule_state: dict[int, bool] = {}    # rule_idx → last truth value

    # ── interlocks (run when parent link dies) ────────────────────────
    def enforce_parent_link_lost(self) -> None:
        interlocks = (self.brain.get("safety") or {}).get("interlocks") or []
        for il in interlocks:
            iid = il.get("id") or f"il-{il.get('applies_to_instance')}"
            if iid in self._interlocks_fired:
                continue
            self._apply(il.get("action") or "off",
                        pin=il.get("board_pin"),
                        value=il.get("value"),
                        reason="parent_link_lost",
                        meta=il)
            self._interlocks_fired.add(iid)
        log.warning("parent link lost — enforced %d interlock(s)", len(interlocks))

    def parent_link_restored(self) -> None:
        if self._interlocks_fired:
            log.info("parent link restored — clearing %d interlock(s)",
                     len(self._interlocks_fired))
        self._interlocks_fired.clear()

    # ── rules (eval each tick against latest telemetry / readings) ────
    def evaluate(self, readings: dict[str, float | int | str],
                 parent_alive: bool) -> None:
        rules = (self.brain.get("safety") or {}).get("rules") or []
        for idx, rule in enumerate(rules):
            critical = bool(rule.get("critical"))
            if not parent_alive and not critical:
                # Non-critical rules don't fire autonomously.
                continue
            when = rule.get("when") or {}
            key = when.get("instance_id") or when.get("channel")
            if key is None or key not in readings:
                continue
            try:
                if not _op_true(when.get("op"), readings[key], when.get("value")):
                    self._rule_state[idx] = False
                    continue
            except (TypeError, ValueError):
                continue
            if self._rule_state.get(idx):
                # Edge-triggered: only fire on FALSE→TRUE transition.
                continue
            then = rule.get("then") or {}
            self._apply(then.get("action") or "alarm_only",
                        pin=rule.get("board_pin"),
                        value=(then.get("params") or {}).get("value"),
                        reason=("rule_critical" if critical else "rule"),
                        meta=rule)
            self._rule_state[idx] = True

    # ── private ─────────────────────────────────────────────────────
    def _apply(self, action: str, pin: str | None,
               value, reason: str, meta: dict) -> None:
        effect = _PIN_EFFECT.get(action)
        if effect is None:
            log.error("unknown action %r — defaulting to off", action)
            effect = ("low", "alarm")
        level, side = effect
        if pin and level in ("low", "high"):
            try:
                self.gpio.set(pin, 1 if level == "high" else 0)
            except Exception as e:
                log.error("gpio.set(%s, %s) failed: %s", pin, level, e)
        elif pin and level == "value" and value is not None:
            try:
                self.gpio.set(pin, value)
            except Exception as e:
                log.error("gpio.set(%s, %r) failed: %s", pin, value, e)
        if side in ("alarm", "shutdown"):
            self.publish_alarm({
                "action": action, "reason": reason, "pin": pin,
                "value": value, "meta": meta,
                "ts": int(time.time()),
            })
        if side == "shutdown":
            log.critical("safe_shutdown fired — triggering os shutdown")
            try:
                self.shutdown()
            except Exception as e:
                log.error("shutdown hook failed: %s", e)


def _op_true(op: str | None, lhs, rhs) -> bool:
    if op is None:
        return False
    # Coerce both sides to numeric where possible so "80" > 70 works.
    def _coerce(v):
        if isinstance(v, (int, float)):
            return v
        try:
            return float(v)
        except (TypeError, ValueError):
            return v
    a, b = _coerce(lhs), _coerce(rhs)
    if op == ">":  return a >  b
    if op == "<":  return a <  b
    if op == ">=": return a >= b
    if op == "<=": return a <= b
    if op == "==": return a == b
    if op == "!=": return a != b
    return False
