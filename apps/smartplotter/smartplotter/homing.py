"""Homing routine — drive each axis slowly toward its limit switch
until the switch trips, retract a fixed distance, drive in slow,
re-trigger, and zero. The standard GRBL / Klipper / LinuxCNC pattern.

Homing happens BEFORE any soft-limit-checked move can run, because
soft limits are only meaningful relative to a known origin.
"""
from __future__ import annotations

import logging
import time

from .motion import MotionPlanner
from .safety import SafetyMonitor


log = logging.getLogger("smartplotter.homing")


def home_axis(planner: MotionPlanner,
              safety: SafetyMonitor,
              axis: str,
              limit_attr: str,
              feed_mm_s: float | None = None,
              retract_mm: float = 5.0) -> bool:
    """Drive `axis` toward its limit, retract, re-approach slowly,
    zero. Returns True on success, False on SafetyAbort / no switch."""
    s = planner.s
    feed = feed_mm_s or s.homing_feed_mm_s

    # 1. Fast approach toward the switch (negative direction by convention).
    log.info("homing: %s fast approach @ %.1f mm/s", axis, feed)
    travel = -max(s.max_x_mm, s.max_y_mm, s.max_z_mm)  # any axis
    try:
        if   axis == "x": planner.move_relative_mm(dx_mm=travel, feed_mm_s=feed)
        elif axis == "y": planner.move_relative_mm(dy_mm=travel, feed_mm_s=feed)
        elif axis == "z": planner.move_relative_mm(dz_mm=travel, feed_mm_s=feed)
    except Exception as e:
        # SafetyAbort here is EXPECTED when the limit switch trips.
        if "limit_hit_" not in str(getattr(safety.state, "fault", "")):
            log.error("homing: %s fast approach failed unexpectedly: %s", axis, e)
            return False

    # If we didn't actually hit the limit, something's wrong.
    hit = getattr(safety.state, f"limit_{axis}_hit", False)
    if not hit:
        log.error("homing: %s never hit its limit switch", axis)
        return False

    # 2. Manual reset: the operator hasn't pressed E-stop, just a limit;
    #    we ack it programmatically since we drove INTO it on purpose.
    setattr(safety.state, f"limit_{axis}_hit", False)
    safety.reset_after_human_check()

    # 3. Retract a fixed distance.
    log.info("homing: %s retract %.1f mm", axis, retract_mm)
    if   axis == "x": planner.move_relative_mm(dx_mm=retract_mm, feed_mm_s=feed)
    elif axis == "y": planner.move_relative_mm(dy_mm=retract_mm, feed_mm_s=feed)
    elif axis == "z": planner.move_relative_mm(dz_mm=retract_mm, feed_mm_s=feed)

    # 4. Slow re-approach.
    slow = max(1.0, feed / 4.0)
    log.info("homing: %s slow re-approach @ %.1f mm/s", axis, slow)
    try:
        if   axis == "x": planner.move_relative_mm(dx_mm=-2 * retract_mm, feed_mm_s=slow)
        elif axis == "y": planner.move_relative_mm(dy_mm=-2 * retract_mm, feed_mm_s=slow)
        elif axis == "z": planner.move_relative_mm(dz_mm=-2 * retract_mm, feed_mm_s=slow)
    except Exception:
        pass

    setattr(safety.state, f"limit_{axis}_hit", False)
    safety.reset_after_human_check()

    # 5. Zero this axis.
    if   axis == "x": planner.x = 0
    elif axis == "y": planner.y = 0
    elif axis == "z": planner.z = 0
    log.info("homing: %s ZEROED", axis)
    return True


def home_all(planner: MotionPlanner, safety: SafetyMonitor) -> dict:
    """Z first (to lift the pen), then X, then Y."""
    results = {}
    results["z"] = home_axis(planner, safety, "z", "limit_z_hit")
    time.sleep(0.5)
    results["x"] = home_axis(planner, safety, "x", "limit_x_hit")
    time.sleep(0.5)
    results["y"] = home_axis(planner, safety, "y", "limit_y_hit")
    return results
