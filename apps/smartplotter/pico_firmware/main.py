"""Pico 2 W firmware entry point for SmartPlotter.

Boots, scans the TMC UART bus for X/Y/Z drivers, and exposes a
newline-delimited JSON protocol on the Pi-facing UART (GP0/GP1).

Pi → Pico commands (one JSON object per line):
  {"cmd": "ping"}                        → {"ack":"pong"}
  {"cmd": "info"}                        → {"drivers": [{address, chip, present, ...}, …]}
  {"cmd": "redetect"}                    → re-scan + return same shape as info
  {"cmd": "current", "axis": "x", "ma": 800}
  {"cmd": "stealthchop", "axis": "x", "on": true}
  {"cmd": "sgthresh", "axis": "x", "value": 90}
  {"cmd": "estop"}                       → drive EN HIGH, return {"estopped": true}
  {"cmd": "release"}                     → clear estop

Pico → Pi async events:
  {"event": "stall", "axis": "x"}
  {"event": "estop", "source": "button"|"watchdog"|"limit_x"|...}
  {"event": "boot",  "uptime_ms": 12, "drivers": [...]}
"""
from machine import UART, Pin
import time
import sys
import select

# Try to import json; some MicroPython builds use ujson
try:
    import json
except ImportError:
    import ujson as json

from tmc import TMC


# ── pin assignments (must match the master pin table) ────────────────
PIN_TMC_UART_TX = 4    # shared half-duplex bus — tx & rx same pin
PIN_TMC_UART_RX = 4
PIN_PI_UART_TX  = 0
PIN_PI_UART_RX  = 1
PIN_DRV_EN      = 12

# Driver UART addresses (set on hardware via MS1/MS2 pads):
#   X = MS1=0 MS2=0 → 0
#   Y = MS1=1 MS2=0 → 1
#   Z = MS1=0 MS2=1 → 2
AXIS_ADDRESS = {"x": 0, "y": 1, "z": 2}


def _setup_uarts():
    tmc_uart = UART(
        1, baudrate=115200,
        tx=Pin(PIN_TMC_UART_TX), rx=Pin(PIN_TMC_UART_RX),
        timeout=50,
    )
    pi_uart = UART(
        0, baudrate=115200,
        tx=Pin(PIN_PI_UART_TX), rx=Pin(PIN_PI_UART_RX),
        timeout=10,
    )
    return tmc_uart, pi_uart


def _send(pi_uart, payload):
    """Write a single newline-delimited JSON object to the Pi."""
    line = json.dumps(payload) + "\n"
    pi_uart.write(line.encode("utf-8"))


def _scan_drivers(tmc_uart) -> list[dict]:
    """Walk the TMC bus and identify each axis's chip."""
    out = []
    for axis, addr in AXIS_ADDRESS.items():
        drv = TMC(tmc_uart, addr)
        info = drv.detect_chip()
        info["axis"] = axis
        out.append(info)
    return out


def _set_estop(en_pin, estopped: bool) -> None:
    # TMC EN is active-LOW: HIGH = disabled. Default at reset is HIGH (safe).
    en_pin.value(1 if estopped else 0)


def main() -> None:
    en = Pin(PIN_DRV_EN, Pin.OUT, value=1)   # boot disabled, always
    tmc_uart, pi_uart = _setup_uarts()
    boot_ms = time.ticks_ms()
    drivers = _scan_drivers(tmc_uart)
    drivers_by_axis = {d["axis"]: TMC(tmc_uart, d["address"])
                       for d in drivers if d["present"]}

    _send(pi_uart, {
        "event": "boot",
        "uptime_ms": time.ticks_diff(time.ticks_ms(), boot_ms),
        "drivers": drivers,
    })

    poll = select.poll()
    poll.register(pi_uart, select.POLLIN)
    buf = b""

    while True:
        events = poll.poll(100)
        if not events:
            continue
        chunk = pi_uart.read(256)
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception as e:
                _send(pi_uart, {"error": "parse", "detail": str(e)})
                continue
            _handle(msg, pi_uart, en, drivers, drivers_by_axis, tmc_uart)


def _handle(msg, pi_uart, en_pin, drivers, drivers_by_axis, tmc_uart):
    cmd = msg.get("cmd")
    if cmd == "ping":
        _send(pi_uart, {"ack": "pong"})
        return
    if cmd == "info":
        _send(pi_uart, {"drivers": drivers})
        return
    if cmd == "redetect":
        drivers[:] = _scan_drivers(tmc_uart)
        drivers_by_axis.clear()
        drivers_by_axis.update({d["axis"]: TMC(tmc_uart, d["address"])
                                for d in drivers if d["present"]})
        _send(pi_uart, {"drivers": drivers})
        return
    if cmd == "estop":
        _set_estop(en_pin, True)
        _send(pi_uart, {"estopped": True})
        return
    if cmd == "release":
        _set_estop(en_pin, False)
        _send(pi_uart, {"estopped": False})
        return

    axis = msg.get("axis")
    drv = drivers_by_axis.get(axis)
    if drv is None:
        _send(pi_uart, {"error": "no_such_axis", "axis": axis})
        return

    try:
        if cmd == "current":
            drv.set_current_ma(int(msg["ma"]))
            _send(pi_uart, {"ack": "current", "axis": axis, "ma": msg["ma"]})
        elif cmd == "stealthchop":
            drv.set_stealthchop(bool(msg["on"]))
            _send(pi_uart, {"ack": "stealthchop", "axis": axis, "on": msg["on"]})
        elif cmd == "sgthresh":
            drv.set_stallguard_threshold(int(msg["value"]))
            _send(pi_uart, {"ack": "sgthresh", "axis": axis, "value": msg["value"]})
        elif cmd == "microsteps":
            drv.set_microsteps(int(msg["value"]))
            _send(pi_uart, {"ack": "microsteps", "axis": axis, "value": msg["value"]})
        elif cmd == "drv_status":
            _send(pi_uart, {"axis": axis, "drv_status": drv.read_drv_status()})
        else:
            _send(pi_uart, {"error": "unknown_cmd", "cmd": cmd})
    except Exception as e:
        _send(pi_uart, {"error": "exec", "cmd": cmd, "detail": str(e)})


main()
