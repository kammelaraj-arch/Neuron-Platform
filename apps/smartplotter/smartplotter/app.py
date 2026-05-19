"""Flask + SocketIO web UI for SmartPlotter.

Endpoints (all require API key in production):
    GET  /                       — index with profile + run list
    GET  /upload                 — upload form
    POST /upload                 — image → contour → profile
    GET  /manual                 — manual jog UI
    GET  /healthz                — public, returns liveness + fault state
    GET  /api/version            — public, returns version + dna
    GET  /api/profiles           — JSON list
    POST /api/profile/{id}/run   — start a run (background task)
    POST /api/profile/{id}/abort — stop the current run
    POST /api/home               — home all axes
    POST /api/move               — jog (relative or absolute)
    POST /api/safety/reset       — clear a software-latched fault
    GET  /api/state              — full state snapshot (safety + run)
    GET  /api/profile/{id}/gcode — export as G-code
"""
from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_socketio import SocketIO

from . import __version__, config
from .auth import require_api_key
from .contour import image_to_contours
from .db import ProfileStore
from .gcode import contours_to_gcode
from .homing import home_all
from .motion import MotionPlanner, run_segments
from .safety import SafetyAbort, SafetyMonitor


log = logging.getLogger("smartplotter.app")


def _build_app(settings):
    app = Flask(__name__, static_folder="../static", template_folder="../templates")
    app.config["SMARTPLOTTER_API_KEY"] = settings.api_key
    app.config["MAX_CONTENT_LENGTH"] = settings.upload_max_mb * 1024 * 1024
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    store = ProfileStore()
    safety = SafetyMonitor(settings, on_fault=lambda s: socketio.emit(
        "safety", {"is_safe": s.is_safe, "fault": s.fault}))
    safety.start()

    # Digital-twin live-position broadcaster. Motion planner calls this
    # at ~40 Hz during a run; we throttle further on the client side if
    # needed. Includes pen state so the canvas can draw pen-down trace
    # vs pen-up jumps differently.
    def _emit_position(x_mm: float, y_mm: float, z_mm: float, pen_down: bool):
        socketio.emit("position", {
            "x_mm": round(x_mm, 3),
            "y_mm": round(y_mm, 3),
            "z_mm": round(z_mm, 3),
            "pen_down": bool(pen_down),
            "ts": time.time(),
        })

    planner = MotionPlanner(settings, safety, position_callback=_emit_position)

    from .mqtt_bridge import MqttBridge
    bridge = MqttBridge(settings, safety)
    bridge.start()

    # ── Run state (single-job at a time) ──────────────────────────────
    run_state = {"profile_id": None, "run_id": None,
                 "completed": 0, "total": 0, "running": False,
                 "abort_requested": False}
    run_lock = threading.Lock()

    # ── Helpers ───────────────────────────────────────────────────────
    @app.context_processor
    def _ctx():
        return {"version": __version__, "settings": settings}

    @app.route("/healthz")
    def healthz():
        return {
            "status": "ok",
            "app": "smartplotter",
            "version": __version__,
            "device_dna": settings.device_dna,
            "is_safe": safety.state.is_safe,
            "fault": safety.state.fault,
            "running": run_state["running"],
        }

    @app.route("/api/version")
    def api_version():
        return {"version": __version__, "app": "smartplotter",
                "device_dna": settings.device_dna}

    @app.route("/api/drivers")
    def api_drivers():
        from .pico_bridge import get_bridge
        bridge = get_bridge()
        return {
            "connected": bridge.connected,
            "device": bridge.device,
            "drivers": bridge.info() if bridge.connected else [],
            "last_error": bridge.last_error,
        }

    @app.route("/api/drivers/redetect", methods=["POST"])
    def api_drivers_redetect():
        from .pico_bridge import get_bridge
        bridge = get_bridge()
        if not bridge.connected:
            bridge.open()
        return {"drivers": bridge.redetect(), "connected": bridge.connected}

    @app.route("/")
    @require_api_key(app)
    def index():
        return render_template("index.html",
                               profiles=store.list_profiles(),
                               runs=store.list_runs(10),
                               safety=safety.state)

    @app.route("/upload", methods=["GET", "POST"])
    @require_api_key(app)
    def upload_page():
        if request.method == "POST":
            f = request.files.get("file")
            if not f:
                abort(400, "no file")
            name = request.form.get("name") or f.filename
            feed = float(request.form.get("feed_mm_s") or 30)
            z_lift = float(request.form.get("z_lift_mm") or 2)
            smoothing = float(request.form.get("smoothing") or 3)
            tmp = Path("/tmp") / f"smartplotter_{int(time.time())}_{f.filename}"
            f.save(tmp)
            contours = image_to_contours(
                tmp, settings.max_x_mm, settings.max_y_mm,
                margin_mm=10.0, smoothing=smoothing,
            )
            tmp.unlink(missing_ok=True)
            if not contours:
                abort(400, "no contours found in image")
            pid = store.insert_profile(name, contours, feed, z_lift)
            bridge.publish_event("profile_created",
                                 {"profile_id": pid, "name": name,
                                  "contour_count": len(contours)})
            return redirect(url_for("index"))
        return render_template("upload.html")

    @app.route("/manual")
    @require_api_key(app)
    def manual_page():
        return render_template("manual.html")

    # ── JSON API ──────────────────────────────────────────────────────
    @app.route("/api/profiles")
    @require_api_key(app)
    def api_profiles():
        return jsonify(store.list_profiles())

    @app.route("/api/profile/<int:pid>/gcode")
    @require_api_key(app)
    def api_profile_gcode(pid: int):
        prof = store.get_profile(pid)
        if not prof:
            abort(404)
        text = contours_to_gcode(prof["contours"],
                                 feed_mm_min=prof["feed_mm_s"] * 60.0,
                                 z_lift_mm=prof["z_lift_mm"])
        return text, 200, {"Content-Type": "text/plain"}

    @app.route("/api/profile/<int:pid>/run", methods=["POST"])
    @require_api_key(app)
    def api_run(pid: int):
        prof = store.get_profile(pid)
        if not prof:
            abort(404)
        with run_lock:
            if run_state["running"]:
                abort(409, "another run is in progress")
            safety.check()  # raises SafetyAbort if not safe
            total = sum(len(c) for c in prof["contours"])
            rid = store.start_run(pid, total)
            run_state.update({"profile_id": pid, "run_id": rid,
                              "completed": 0, "total": total,
                              "running": True, "abort_requested": False})
            socketio.start_background_task(_run_profile, prof, rid)
        bridge.publish_event("profile_started",
                             {"profile_id": pid, "run_id": rid, "total": total})
        return {"ok": True, "run_id": rid, "total": total}

    @app.route("/api/profile/<int:pid>/abort", methods=["POST"])
    @require_api_key(app)
    def api_abort(pid: int):
        with run_lock:
            if not run_state["running"] or run_state["profile_id"] != pid:
                return {"ok": False, "reason": "no run in progress for that profile"}
            run_state["abort_requested"] = True
        safety.trigger_safe_stop("operator_abort")
        return {"ok": True}

    @app.route("/api/home", methods=["POST"])
    @require_api_key(app)
    def api_home():
        results = home_all(planner, safety)
        return {"ok": all(results.values()), "results": results}

    @app.route("/api/move", methods=["POST"])
    @require_api_key(app)
    def api_move():
        body = request.get_json(silent=True) or {}
        mode = body.get("mode", "relative")
        feed = body.get("feed_mm_s")
        try:
            if mode == "absolute":
                planner.move_to_mm(x_mm=body.get("x"), y_mm=body.get("y"),
                                   z_mm=body.get("z"), feed_mm_s=feed)
            else:
                planner.move_relative_mm(
                    dx_mm=float(body.get("dx", 0)),
                    dy_mm=float(body.get("dy", 0)),
                    dz_mm=float(body.get("dz", 0)),
                    feed_mm_s=feed)
        except SafetyAbort as e:
            return {"ok": False, "reason": str(e)}, 409
        return {"ok": True, "x_mm": planner.x / settings.steps_per_mm,
                "y_mm": planner.y / settings.steps_per_mm,
                "z_mm": planner.z / settings.steps_per_mm}

    @app.route("/api/safety/reset", methods=["POST"])
    @require_api_key(app)
    def api_safety_reset():
        safety.reset_after_human_check()
        return {"ok": safety.state.is_safe, "fault": safety.state.fault}

    @app.route("/api/pinmap")
    @require_api_key(app)
    def api_pinmap():
        """Return the resolved pin map + the source per pin so the
        operator can verify what's wired without docker exec'ing."""
        from .pins import (
            DEFAULT_PIN_MAP, _load_from_brain_json, _load_from_env,
        )
        from_brain = _load_from_brain_json(settings.bundle_dir)
        from_env   = _load_from_env()
        rows = []
        for key in sorted(set(DEFAULT_PIN_MAP) | set(from_brain) | set(from_env)):
            if   key in from_env:   src = "env"
            elif key in from_brain: src = "brain.json"
            else:                   src = "default"
            rows.append({
                "logical":     key,
                "bcm":         planner.pins.get(key),
                "source":      src,
                "default_bcm": DEFAULT_PIN_MAP.get(key),
            })
        return {
            "device_dna": settings.device_dna,
            "bundle_dir": str(settings.bundle_dir),
            "pins": rows,
        }

    @app.route("/api/state")
    @require_api_key(app)
    def api_state():
        return {
            "version": __version__,
            "device_dna": settings.device_dna,
            "safety": {
                "is_safe": safety.state.is_safe,
                "fault": safety.state.fault,
                "estop_pressed": safety.state.estop_pressed,
                "limit_x_hit": safety.state.limit_x_hit,
                "limit_y_hit": safety.state.limit_y_hit,
                "limit_z_hit": safety.state.limit_z_hit,
                "parent_link_lost": safety.state.parent_link_lost,
            },
            "position_mm": {
                "x": planner.x / settings.steps_per_mm,
                "y": planner.y / settings.steps_per_mm,
                "z": planner.z / settings.steps_per_mm,
            },
            "run": dict(run_state),
        }

    # ── Background runner ─────────────────────────────────────────────
    def _run_profile(prof: dict, run_id: int):
        completed = 0
        status = "ok"
        fault = None
        try:
            for ci, contour in enumerate(prof["contours"]):
                if run_state["abort_requested"]:
                    status = "aborted"; fault = "operator_abort"; break
                def progress(i: int, n: int) -> None:
                    nonlocal completed
                    completed += 1
                    store.update_run(run_id, completed)
                    socketio.emit("progress", {
                        "profile_id": prof["id"], "run_id": run_id,
                        "completed": completed, "total": run_state["total"],
                        "pct": int(completed * 100 / max(1, run_state["total"]))
                    })
                run_segments(planner, contour,
                             feed_mm_s=prof["feed_mm_s"],
                             z_lift_mm=prof["z_lift_mm"],
                             progress_cb=progress)
        except SafetyAbort as e:
            status = "safety_abort"; fault = str(e)
        except Exception as e:
            status = "error"; fault = f"{type(e).__name__}: {e}"
            log.exception("run failed")
        finally:
            store.end_run(run_id, status=status, fault=fault)
            with run_lock:
                run_state.update({"running": False, "abort_requested": False})
            bridge.publish_event("profile_ended",
                                 {"profile_id": prof["id"], "run_id": run_id,
                                  "status": status, "fault": fault})
            socketio.emit("done", {"run_id": run_id, "status": status,
                                   "fault": fault})

    return app, socketio


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = config.load()
    log.info("SmartPlotter v%s starting — dna=%s broker=%s",
             __version__, settings.device_dna, settings.broker_url or "<disabled>")
    app, socketio = _build_app(settings)
    socketio.run(app, host=settings.bind_host, port=settings.bind_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
