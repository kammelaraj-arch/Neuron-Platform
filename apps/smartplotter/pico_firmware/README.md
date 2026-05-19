# SmartPlotter Pico firmware

MicroPython firmware for a Raspberry Pi Pico 2 W acting as the real-time
co-processor for SmartPlotter (XYZ stepping, safety, motor I/O).

## What it does

- Detects which TMC chip is on each axis at boot (TMC2208/2209/2226/2240)
  by reading `IOIN.VERSION`. The Pi-side app picks this up over UART and
  shows "TMC2226 on axis X" in the UI instead of guessing.
- Speaks newline-delimited JSON to the Pi on GP0/GP1.
- Drives all three TMC drivers over one shared UART line (GP4, half-duplex,
  addressed by MS1/MS2 hardware pads).
- Exposes safe defaults: drivers start **disabled** (EN HIGH at boot).
  Pi must explicitly release after init.

## Flashing

You need [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html)
on a host machine that can reach the Pico over USB.

```bash
# Install mpremote
pip install --user mpremote

# Pico connected via USB, in BOOTSEL? No — for MicroPython flashing,
# install MicroPython firmware first (one-off) by holding BOOTSEL and
# copying the .uf2 from https://micropython.org/download/RPI_PICO2_W/

# After MicroPython is on the Pico, upload our app code:
cd apps/smartplotter/pico_firmware
mpremote connect auto cp tmc.py :
mpremote connect auto cp main.py :
mpremote connect auto reset
```

The Pico will boot, scan the TMC bus, and emit its boot event on its
Pi-facing UART (GP0/GP1). Once you wire that to the Pi 5's UART pins
(BCM 14/15) and start the SmartPlotter Flask app, the Pi will pick up
the drivers list automatically.

## Pin assignments (see master pinmap)

| Pico GP | Use |
|---|---|
| 0 / 1 | UART0 ↔ Pi 5 (TX, RX) |
| 4 | TMC half-duplex UART (shared bus to X/Y/Z PDN_UART pads) |
| 12 | Shared `EN` line to all 3 TMC drivers + TB6612 STBY |
| 13 / 14 | X STEP / DIR |
| 15 / 26 | Y STEP / DIR |
| 27 / 28 | Z STEP / DIR |
| 2 / 3 / 22 | DIAG inputs (StallGuard) for X / Y / Z |
| 5 | E-stop button input |
| 6–11 | TB6612FNG IN1..4 + PWMA/B |
| 16 / 17 | I²C0 SDA / SCL → PCA9685 |
| 18–21 | 4-channel relay IN1..4 |

## Pi ↔ Pico protocol

One JSON object per line. Pico echoes a reply line per command.

### Commands

```json
{"cmd": "ping"}                                  // → {"ack": "pong"}
{"cmd": "info"}                                  // → {"drivers": [...]}
{"cmd": "redetect"}                              // re-scan the TMC bus
{"cmd": "current",     "axis": "x", "ma": 800}
{"cmd": "stealthchop", "axis": "x", "on": true}  // false = SpreadCycle for SG
{"cmd": "sgthresh",    "axis": "x", "value": 90}
{"cmd": "microsteps",  "axis": "x", "value": 16} // 256, 128, 64, 32, 16, 8, 4, 2
{"cmd": "drv_status",  "axis": "x"}              // → DRV_STATUS register
{"cmd": "estop"}                                 // EN HIGH = motors off
{"cmd": "release"}                               // EN LOW = motors armed
```

### Async events

```json
{"event": "boot",  "uptime_ms": 12, "drivers": [...]}
{"event": "stall", "axis": "x"}
{"event": "estop", "source": "button"|"watchdog"|"limit_x"|...}
```

### Drivers list shape

Returned by `info` / `redetect` and embedded in the `boot` event:

```json
[
  {"axis":"x","address":0,"chip":"TMC2226","version_byte":34,"present":true},
  {"axis":"y","address":1,"chip":"TMC2209","version_byte":33,"present":true},
  {"axis":"z","address":2,"chip":"absent", "version_byte":null,"present":false}
]
```

`chip` is one of `TMC2208 | TMC2209 | TMC2226 | TMC2240 | unknown_0xNN | absent`.

## Why autodetect matters

You can hot-swap a TMC2209 carrier with a TMC2226 (same footprint, same pinout)
without changing any firmware. The Pico reads `IOIN.VERSION` on boot, tells
the Pi which chip it found, and the UI updates accordingly. Useful when:

- mixing driver vintages across axes (e.g. spare drawer + new order)
- diagnosing a dead driver (`present: false` instantly flags it)
- ensuring firmware doesn't issue TMC2240-only registers to an older 2209

## Safety invariants

1. `EN` (GP12) defaults HIGH at every reset — motors are OFF until the Pi
   sends `release` over the UART link.
2. If the Pi-facing UART goes silent for > 1 second, the Pico re-asserts
   `EN` HIGH (watchdog failsafe). Re-enable requires a fresh `release`.
3. E-stop press (GP5) → ISR drives `EN` HIGH AND posts an `estop` event.
   Release of the mushroom does **not** auto-rearm; Pi must `release`.
