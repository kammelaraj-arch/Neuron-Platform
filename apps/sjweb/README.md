# sjweb — Stepper XYZ Plotter (Pi 4 + 4× DRV8825)

Flask-SocketIO web app that turns uploaded images into traced
plots. Drop an image (jalebi spiral, logo, human silhouette, line
drawing, anything) onto the web UI, the app extracts contours with
OpenCV and executes them as XY motor steps with optional Z lift.

Designed for a **Raspberry Pi 4 / Pi 5** with a **CNC Shield V3** or
equivalent 4× DRV8825 carrier wired for **X / dual-Y / Z** (the dual-Y
matches the long-axis pen-plotter pattern).

## What's in the box

- `sjweb.py`               — Flask + SocketIO entry point, drives the plot
- `StepperHelper.py`       — wraps `RPi.GPIO`, exposes
                             `smooth_move_interp(x_steps, x_dir, y_steps, y_dir, speed)`
                             and `jog_axis('z', steps, dir, speed)`
- `DBHelper.py`            — sqlite-backed profile / step storage
- `filehelper.py`          — upload handling
- `templates/`             — index / upload / manual pages
- `static/`                — CSS, JS

## Quick deploy via the Neuron platform

1. Open **`/ui/apps`** on the master.
2. Find **APP-0001 — Stepper XYZ Plotter (sjweb)**.
3. Click **🚀 Deploy** and paste the target group's UUID (the EdgeGroup
   that owns the Pi 4 + DRV8825 driver bundle).
4. The master SSHes to the Pi using the saved credentials, installs
   dependencies, copies the app into `/opt/sjweb/`, writes the systemd
   unit, runs `systemctl enable --now`, and verifies the service is
   active.
5. Browse to `http://<pi>.local:5000/` — upload an image and plot.

## Manual install on a fresh Pi OS

```bash
sudo apt update
sudo apt install -y python3-pip python3-opencv libatlas-base-dev
sudo pip3 install --break-system-packages flask flask-socketio eventlet RPi.GPIO numpy

sudo cp -r sjweb /opt/sjweb
sudo cp neuron-app-sjweb.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now neuron-app-sjweb
```

Then visit `http://<pi-ip>:5000/`.

## Pin mapping (DRV8825 on CNC Shield V3)

Wire the Pi 4 to the CNC Shield V3 with the GRBL pinout. The defaults
in `StepperHelper.py`:

| Pi GPIO  | Shield pin | Function       |
|----------|------------|----------------|
| GPIO17   | X-STEP     | X-axis step    |
| GPIO27   | X-DIR      | X-axis dir     |
| GPIO22   | Y-STEP     | Y-axis step    |
| GPIO23   | Y-DIR      | Y-axis dir     |
| GPIO24   | Y2-STEP    | Y dual-axis step (slave) |
| GPIO25   | Y2-DIR     | Y dual-axis dir |
| GPIO5    | Z-STEP     | Z-axis step    |
| GPIO6    | Z-DIR      | Z-axis dir     |
| GPIO12   | EN (all)   | Driver enable (active LOW) |

Override the BCM pin map by editing the constants at the top of
`StepperHelper.py` or by setting env vars before the service starts.

## Profile storage

Each upload becomes a *profile* in the local SQLite (`sjweb.db`) with
the precomputed step list. The index page lists every saved profile;
click **▶ Run** to re-execute one without re-uploading.

## Source

Catalogue entry: APP-0001 in `/ui/apps`.
Repo path:        `apps/sjweb/` in the Neuron-Platform monorepo.
