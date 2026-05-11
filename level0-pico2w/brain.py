"""Minimal on-device Brain runtime.

Reads ``brain.json`` (written by the Master firmware bundle), runs each loop
on its declared period, evaluates safety interlocks before applying outputs,
and exposes a tiny ``apply_desired`` entry point so the main loop can push
desired-state updates from the Edge into the active loops.
"""
import json
import time

import logger
import trace


class _PIDState:
    def __init__(self, kp=1.0, ki=0.0, kd=0.0, out_min=0.0, out_max=100.0):
        self.kp = kp; self.ki = ki; self.kd = kd
        self.out_min = out_min; self.out_max = out_max
        self.integral = 0.0
        self.prev_err = 0.0
        self.last_t = None

    def step(self, sp, pv, now):
        dt = 0.0
        if self.last_t is not None:
            dt = max(0.0, now - self.last_t)
        self.last_t = now
        err = sp - pv
        self.integral += err * dt
        deriv = (err - self.prev_err) / dt if dt > 0 else 0.0
        self.prev_err = err
        out = self.kp * err + self.ki * self.integral + self.kd * deriv
        if out > self.out_max: out = self.out_max
        if out < self.out_min: out = self.out_min
        return out


class Brain:
    def __init__(self, path="brain.json"):
        self._cfg = self._load(path)
        self._loops = list(self._cfg.get("loops", []))
        self._safety = self._cfg.get("safety", {})
        self._safe_state = self._safety.get("safe_state", {}) or {}
        self._interlocks = self._safety.get("interlocks", []) or []
        self._next_run_ms = {l["id"]: 0 for l in self._loops}
        self._pid = {l["id"]: _PIDState(**(l.get("params") or {})) for l in self._loops if l.get("type") == "pid"}
        self._desired = {}
        self._reported = {}
        self._current = {"alarm_active": False}

    @staticmethod
    def _load(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception:
            return {"loops": [], "safety": {}, "telemetry": {}, "offline_policy": {}}

    def safe_state(self):
        return dict(self._safe_state)

    def apply_desired(self, desired):
        if not isinstance(desired, dict):
            return
        self._desired.update(desired)

    def update_inputs(self, inputs):
        if isinstance(inputs, dict):
            self._reported.update(inputs)

    def _trip_check(self):
        # Evaluate interlocks. We support a tiny expression language:
        #   "X > Y", "X < Y", "X is null"
        # where X is taken from reported and Y from desired (then literals).
        for il in self._interlocks:
            cond = il.get("condition", "")
            if "is null" in cond:
                key = cond.split(" ")[0].strip()
                if self._reported.get(key) is None:
                    return il
                continue
            for op in (">=", "<=", ">", "<", "=="):
                if op in cond:
                    lhs, rhs = (s.strip() for s in cond.split(op, 1))
                    lv = self._reported.get(lhs)
                    rv = self._desired.get(rhs, None)
                    if rv is None:
                        try:
                            rv = float(rhs)
                        except ValueError:
                            rv = None
                    if lv is None or rv is None:
                        continue
                    cmp_ok = (
                        (op == ">" and lv > rv) or
                        (op == "<" and lv < rv) or
                        (op == ">=" and lv >= rv) or
                        (op == "<=" and lv <= rv) or
                        (op == "==" and lv == rv)
                    )
                    if cmp_ok:
                        return il
                    break
        return None

    def step(self):
        """Run one tick. Returns the dict of outputs to apply."""
        now_ms = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.time() * 1000)
        outputs = dict(self._safe_state)

        tripped = self._trip_check()
        if tripped is not None:
            trace.add("interlock_trip", id=tripped.get("id"))
            self._current["alarm_active"] = True
            return outputs

        self._current["alarm_active"] = False

        for loop in self._loops:
            lid = loop["id"]
            period_ms = int(loop.get("period_ms", 1000))
            next_at = self._next_run_ms.get(lid, 0)
            if now_ms < next_at:
                continue
            self._next_run_ms[lid] = (
                time.ticks_add(now_ms, period_ms)
                if hasattr(time, "ticks_add")
                else now_ms + period_ms
            )

            ltype = loop.get("type")
            if ltype == "pid":
                pid = self._pid[lid]
                sp = self._desired.get("setpoint_c", 0.0)
                pv = self._reported.get("process_temp_c", 0.0) or 0.0
                duty = pid.step(sp, pv, now_ms / 1000.0)
                outputs["ssr_duty"] = duty
            elif ltype == "passthrough":
                # Pass through reported values as outputs (stable_weight, etc.).
                pass
            elif ltype == "state_machine":
                cmd = self._desired.get("command")
                if cmd in ("start", "stop"):
                    outputs["motor_running"] = cmd == "start"

        self._current.update(outputs)
        return outputs

    def snapshot(self):
        return {
            "desired": dict(self._desired),
            "reported": dict(self._reported),
            "current": dict(self._current),
        }
