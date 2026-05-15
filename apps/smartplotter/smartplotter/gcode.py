"""G-code export/import — minimum viable for interop with LinuxCNC /
GRBL / Klipper. Lets the operator hand a profile to a different
controller, or import G-code from any tool that emits it (Inkscape's
gcodetools, vpype, etc.)."""
from __future__ import annotations


def contours_to_gcode(
    contours: list[list[tuple[float, float]]],
    feed_mm_min: float = 1500.0,
    z_lift_mm: float = 2.0,
    z_draw_mm: float = 0.0,
) -> str:
    """Standard pen-plotter dialect: G21 mm, G90 absolute, G0 / G1
    moves, F = feed in mm/min."""
    out = [
        "; SmartPlotter G-code export",
        "G21          ; mm units",
        "G90          ; absolute positioning",
        f"G1 Z{z_lift_mm:.3f} F{feed_mm_min:.0f}   ; pen up",
    ]
    for c in contours:
        if not c:
            continue
        x0, y0 = c[0]
        out.append(f"G0 X{x0:.3f} Y{y0:.3f}        ; travel to start")
        out.append(f"G1 Z{z_draw_mm:.3f} F{feed_mm_min:.0f}   ; pen down")
        for x, y in c[1:]:
            out.append(f"G1 X{x:.3f} Y{y:.3f} F{feed_mm_min:.0f}")
        out.append(f"G1 Z{z_lift_mm:.3f} F{feed_mm_min:.0f}   ; pen up")
    out.append("M2           ; end of program")
    return "\n".join(out) + "\n"


def gcode_to_segments(text: str) -> list[tuple[str, dict[str, float]]]:
    """Parse a minimal subset of G-code (G0 / G1 / G21 / G90 / M2 / Z /
    X / Y / F) into a list of (op, params) tuples for the motion
    planner. Anything else is logged + skipped."""
    out: list[tuple[str, dict[str, float]]] = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        op = tokens[0].upper()
        params: dict[str, float] = {}
        for tok in tokens[1:]:
            if len(tok) < 2:
                continue
            try:
                params[tok[0].upper()] = float(tok[1:])
            except ValueError:
                pass
        out.append((op, params))
    return out
