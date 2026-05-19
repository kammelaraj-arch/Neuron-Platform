#!/usr/bin/env bash
# Upload SmartPlotter Pico firmware via mpremote.
#
# Prereqs:
#   - MicroPython already flashed on the Pico (one-off, hold BOOTSEL +
#     copy the .uf2 from micropython.org/download/RPI_PICO2_W/)
#   - mpremote installed: pip install --user mpremote
#   - Pico connected via USB (any /dev/ttyACM* or auto)
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v mpremote >/dev/null 2>&1; then
  echo "mpremote not found. Install with: pip install --user mpremote" >&2
  exit 1
fi

echo "→ Connecting to Pico…"
mpremote connect auto eval "print('Pico is alive')" || {
  echo "Could not reach the Pico. Is it plugged in over USB and running MicroPython?" >&2
  exit 1
}

echo "→ Uploading tmc.py…"
mpremote connect auto cp tmc.py :

echo "→ Uploading main.py…"
mpremote connect auto cp main.py :

echo "→ Soft-resetting Pico…"
mpremote connect auto reset

echo "✓ Flash complete. Reading first 5 lines of boot output:"
timeout 5 mpremote connect auto repl --inject-code "import time; time.sleep(1)" || true
