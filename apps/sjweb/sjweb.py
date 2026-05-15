#!/usr/bin/env python3
"""sjweb — Stepper XYZ Plotter web UI.

Flask + SocketIO server that turns uploaded images into traced plots
on a 4× DRV8825 dual-Y XYZ plotter. Imported into the Neuron Platform
catalogue as APP-0001. See README.md for wiring + deployment.
"""
import os
import time
import threading
import atexit

from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit

import cv2
import numpy as np

from DBHelper import DBHelper
from filehelper import FileHelper
from StepperHelper import StepperHelper


# ─── Config ───────────────────────────────────────────────────────────────
DEBUG = bool(int(os.environ.get("JWEB_DEBUG", "0")))


def debug_log(msg: str) -> None:
    if DEBUG:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[DEBUG {ts}] {msg}")


# ─── Singletons ──────────────────────────────────────────────────────────
db = DBHelper()
fh = FileHelper()
stepper = StepperHelper(dual_y=True, debug=DEBUG)

app = Flask(__name__, static_folder="static", template_folder="templates")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

LOG_LOCK = threading.Lock()


def app_log(msg: str, room: str | None = None) -> None:
    with LOG_LOCK:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        s = f"[{ts}] {msg}"
        print(s)
        debug_log(f"app_log: {s} (room={room})")
        try:
            if room:
                socketio.emit("log", {"msg": s}, to=room)
            else:
                socketio.emit("log", {"msg": s})
        except Exception as e:
            debug_log(f"SocketIO emit failed: {e}")


# ─── Image → step sequence ──────────────────────────────────────────────
def image_to_steps(image_path, scale=1.0, smoothing=3):
    """Read `image_path`, find external contours, walk each contour and
    emit a sequence of (|dx|, |dy|, x_dir, y_dir) tuples ready for the
    stepper driver."""
    debug_log(f"image_to_steps image_path={image_path} scale={scale} smoothing={smoothing}")
    img = cv2.imread(image_path)
    if img is None:
        debug_log("image_to_steps: image is None")
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        debug_log("image_to_steps: no contours found")
        return []
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    steps = []
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        eps = max(1.0, smoothing * peri / 100.0)
        approx = cv2.approxPolyDP(contour, eps, True)
        pts = approx.reshape(-1, 2)
        if len(pts) < 2:
            continue
        dense = []
        for i in range(len(pts)):
            p0, p1 = pts[i], pts[(i + 1) % len(pts)]
            dist = int(max(1, np.hypot(p1[0] - p0[0], p1[1] - p0[1])))
            for t in np.linspace(0, 1, max(2, dist)):
                x = p0[0] + (p1[0] - p0[0]) * t
                y = p0[1] + (p1[1] - p0[1]) * t
                dense.append((x, y))
        prev_x, prev_y = dense[0]
        for xf, yf in dense[1:]:
            dx = int(round((xf - prev_x) * scale))
            dy = int(round((yf - prev_y) * scale))
            if dx == 0 and dy == 0:
                prev_x, prev_y = xf, yf
                continue
            x_dir = 1 if dx >= 0 else 0
            y_dir = 1 if dy >= 0 else 0
            steps.append((abs(dx), abs(dy), x_dir, y_dir))
            prev_x, prev_y = xf, yf
    debug_log(f"image_to_steps: {len(steps)} steps")
    return steps


# ─── Profile execution ──────────────────────────────────────────────────
def execute_profile_background(profile_id, sid):
    debug_log(f"execute_profile_background profile_id={profile_id} sid={sid}")
    try:
        profile = db.get_profile(profile_id)
        if not profile:
            app_log(f"No profile {profile_id}", room=sid)
            socketio.emit("done", {"profile_id": profile_id}, to=sid)
            return
        name = profile.get("name", "Profile")
        app_log(f"Executing profile '{name}'", room=sid)
        steps_rows = db.get_profile_steps(profile_id)
        total = len(steps_rows)
        if total == 0:
            app_log("Profile empty", room=sid)
            socketio.emit("done", {"profile_id": profile_id}, to=sid)
            return
        for idx, row in enumerate(steps_rows):
            _, x_step, y_step, x_dir, y_dir = row
            stepper.smooth_move_interp(
                int(x_step), int(x_dir), int(y_step), int(y_dir), speed=5,
            )
            if idx % 25 == 0 or idx == total - 1:
                pct = int((idx + 1) / total * 100)
                socketio.emit(
                    "progress",
                    {"profile_id": profile_id, "index": idx, "total": total, "pct": pct},
                    to=sid,
                )
        app_log(f"Profile '{name}' finished", room=sid)
        socketio.emit("done", {"profile_id": profile_id}, to=sid)
    except Exception as e:
        app_log(f"Execution error: {e}", room=sid)
        socketio.emit("error", {"msg": str(e)}, to=sid)


# ─── Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    profiles_raw = db.list_profiles()
    profiles = [
        {"id": r[0], "name": r[1], "scale": r[2], "smoothing": r[3], "steps": r[4]}
        for r in profiles_raw
    ]
    return render_template("index.html", profiles=profiles)


@app.route("/upload", methods=["GET", "POST"])
def upload_page():
    if request.method == "POST":
        f = request.files.get("file")
        if not f:
            return "No file", 400
        name = request.form.get("name") or f.filename
        scale = float(request.form.get("scale") or 1.0)
        smoothing = int(request.form.get("smoothing") or 3)
        saved = fh.save_upload(f, filename=f"{int(time.time())}_{f.filename}")
        app_log(f"Upload saved: {saved}")
        steps = image_to_steps(saved, scale=scale, smoothing=smoothing)
        if not steps:
            return "No contour found", 400
        profile_id = db.insert_profile(name, scale, smoothing, steps)
        app_log(f"Profile {name} ({profile_id}) created with {len(steps)} steps")
        return redirect(url_for("index"))
    return render_template("upload.html")


@app.route("/manual")
def manual_page():
    return render_template("manual.html")


@app.route("/healthz")
def healthz():
    return {"status": "ok", "app": "sjweb", "version": "1.0.0"}


# ─── SocketIO ───────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    app_log(f"Client connected: {request.sid}")


@socketio.on("disconnect")
def on_disconnect():
    app_log(f"Client disconnected: {request.sid}")


@socketio.on("list_profiles")
def on_list_profiles():
    emit("profiles", db.list_profiles())


@socketio.on("run_profile")
def on_run_profile(data):
    pid = data.get("profile_id")
    if pid is None:
        emit("error", {"msg": "profile_id missing"})
        return
    try:
        pid = int(pid)
    except Exception:
        emit("error", {"msg": "invalid profile_id"})
        return
    sid = request.sid
    app_log(f"Run requested for profile {pid}", room=sid)
    socketio.start_background_task(execute_profile_background, pid, sid)
    emit("started", {"profile_id": pid})


@socketio.on("manual_control")
def on_manual_control(data):
    direction = data.get("dir")
    dist = float(data.get("distance", 10))
    speed = int(data.get("speed", 5))
    steps_per_mm = 80
    steps = int(dist * steps_per_mm)
    app_log(
        f"Manual: dir={direction} dist={dist}mm steps={steps} speed={speed}",
        room=request.sid,
    )
    if direction == "up":
        stepper.smooth_move_interp(0, 0, steps, 1, speed=speed)
    elif direction == "down":
        stepper.smooth_move_interp(0, 0, steps, 0, speed=speed)
    elif direction == "left":
        stepper.smooth_move_interp(steps, 0, 0, 0, speed=speed)
    elif direction == "right":
        stepper.smooth_move_interp(steps, 1, 0, 0, speed=speed)
    elif direction == "zup":
        stepper.jog_axis("z", steps, 1, speed=speed)
    elif direction == "zdown":
        stepper.jog_axis("z", steps, 0, speed=speed)
    emit("jogbed", {"axis": direction, "steps": steps, "speed": speed})


# ─── Cleanup ─────────────────────────────────────────────────────────────
def cleanup_all():
    stepper.cleanup()


atexit.register(cleanup_all)


if __name__ == "__main__":
    print("Starting sjweb on http://0.0.0.0:5000")
    debug_log("Debug mode is ON" if DEBUG else "Debug mode is OFF")
    socketio.run(app, host="0.0.0.0", port=5000)
