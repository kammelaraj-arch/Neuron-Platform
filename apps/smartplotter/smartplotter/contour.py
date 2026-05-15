"""Image → contour pipeline. OpenCV's findContours + a dense sampler.

Returns a list of XY point sequences (one per contour) in mm space,
scaled to fit within the configured bed envelope. Distinct from
motion: this layer is pure geometry, ready for `motion.run_segments`.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Iterable


log = logging.getLogger("smartplotter.contour")


def image_to_contours(
    image_path: str | Path,
    max_x_mm: float,
    max_y_mm: float,
    margin_mm: float = 10.0,
    smoothing: float = 3.0,
    min_contour_area: int = 200,
) -> list[list[tuple[float, float]]]:
    """Returns list of contours; each contour is a list of (x_mm, y_mm)
    points. Largest contour first."""
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        log.error("opencv / numpy missing: %s", e)
        return []

    img = cv2.imread(str(image_path))
    if img is None:
        log.error("could not read %s", image_path)
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_NONE)
    if not contours:
        return []
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    contours = [c for c in contours if cv2.contourArea(c) >= min_contour_area]
    if not contours:
        return []

    # Compute the bounding box of all kept contours so we can scale to
    # fit the bed envelope (preserving aspect).
    all_pts = np.vstack([c.reshape(-1, 2) for c in contours]).astype(float)
    x_lo, y_lo = all_pts.min(axis=0)
    x_hi, y_hi = all_pts.max(axis=0)
    width  = max(1.0, x_hi - x_lo)
    height = max(1.0, y_hi - y_lo)
    avail_x = max_x_mm - 2 * margin_mm
    avail_y = max_y_mm - 2 * margin_mm
    scale = min(avail_x / width, avail_y / height)
    log.info("contour: bbox=%.0fx%.0f, scale=%.4f → fits %.1fx%.1f mm",
             width, height, scale, width * scale, height * scale)

    out: list[list[tuple[float, float]]] = []
    for c in contours:
        peri = float(cv2.arcLength(c, True))
        eps = max(1.0, smoothing * peri / 100.0)
        approx = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(approx) < 2:
            continue
        # Dense-sample each segment so a step-rate-aware motion planner
        # can interpolate cleanly without skipping detail.
        dense: list[tuple[float, float]] = []
        for i in range(len(approx)):
            p0 = approx[i]
            p1 = approx[(i + 1) % len(approx)]
            dist = max(1.0, math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
            n_segments = max(2, int(dist / 2))   # one sample every ~2 px
            for t in (j / (n_segments - 1) for j in range(n_segments)):
                px = p0[0] + (p1[0] - p0[0]) * t
                py = p0[1] + (p1[1] - p0[1]) * t
                # Origin shift + scale + margin
                mx = (px - x_lo) * scale + margin_mm
                # Flip Y so image-top maps to bed-far-edge (mirrors what
                # operators expect on a pen plotter).
                my = (y_hi - py) * scale + margin_mm
                dense.append((mx, my))
        out.append(dense)
    return out


def contours_flat(contours: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Flatten N contours into one point list with implicit pen-up
    between contours. The MotionPlanner.run_segments helper handles
    pen-up/-down at the boundaries of this flat list — for multi-
    contour plots, run each contour separately and let the planner do
    the lift between."""
    return [pt for c in contours for pt in c]
