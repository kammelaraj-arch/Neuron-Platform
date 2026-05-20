# Neuron Pico 2 W Baseline (Layer 1)

The canonical platform layer every Pico-based Neuron app sits on top of.
Provides identity, security, comms, safety, and reliability primitives
so app authors don't have to (and can't bypass).

## Layers

```
┌──────────────────────────────────────────────┐
│  Layer 2  app/main.py + app/* — application │
├──────────────────────────────────────────────┤
│  Layer 1  neuron/ — this package             │
├──────────────────────────────────────────────┤
│  Layer 0  MicroPython runtime (.uf2)         │
└──────────────────────────────────────────────┘
   plus  config.json — per-Pico identity + creds
```

## What's in this package

| Module | Responsibility |
|---|---|
| `config.py`     | Load + validate config.json (fails closed on missing keys) |
| `identity.py`   | Device DNA, hardware UUID, reset cause |
| `security.py`   | Host allow-list + TLS cert SHA-256 pinning |
| `integrity.py`  | Config-HMAC + signed-app-manifest verification |
| `api.py`        | HTTP client with allow-list, pin, API key, bounded retry |
| `channels.py`   | 4-channel comms manager (control, emergency, OTA, UART) |
| `watchdog.py`   | Parent-link watchdog (UART silence → failsafe) |
| `hwdog.py`      | RP2350 hardware watchdog (firmware lockup → reset) |
| `failsafe.py`   | Handler registry: apps register, baseline fires |
| `led.py`        | Onboard-LED state codes (boot/ok/degraded/failsafe/fault) |
| `crashlog.py`   | Persistent traceback log, shipped on next boot |
| `wifi.py`       | WiFi auto-reconnect with exponential backoff |
| `ntp.py`        | RTC sync via NTP (fire-and-forget after WiFi up) |
| `telemetry.py`  | Batched HTTP publish (ring buffer, drop-oldest) |

## Hard rules enforced (per CLAUDE.md)

1. **Parent-only communication.** No inbound listening services; no SSH;
   no telnet; no HTTP server. All comms are Pico-initiated outbound.
2. **Default-safe boot.** EN-pin defaults HIGH; motors disabled until
   the app layer explicitly enables them after parent says `release`.
3. **Four channels always.** Bundle generator refuses to emit a config
   missing any of: control, emergency, OTA, uart_local.
4. **State changes auto-push.** Apps don't have to opt in; baseline
   wraps every state-mutating event into a telemetry publish.
5. **Bidirectional heartbeat.** Parent watchdog trips failsafe on UART
   silence; Pico heartbeats to master so master can flag offline.
6. **Local-brain failsafe contract.** Per-component failsafe actions
   (off / hold_last / go_to_safe_value / alarm_only) enforced
   autonomously when comms degrade, regardless of network state.

## Security posture

| Threat | Mitigation |
|---|---|
| Compromised app exfiltrates to third party | Host allow-list rejects |
| MITM with leaked CA | TLS SHA-256 pin rejects |
| Tampered config.json (changed allow-list, API key) | HMAC fails at boot |
| Tampered app code | Signed app manifest fails at boot |
| Stolen Pico's API key used elsewhere | Master rejects: DNA bound to key owner |
| Firmware lockup | Hardware watchdog auto-resets after 8 s |
| Power-supply glitch | Brown-out reset cause logged, shipped upstream |
| Lost parent | UART watchdog trips per-component failsafe |

## What's NOT in the baseline (by design)

| Out of scope | Why |
|---|---|
| SSH server | Inbound port forbidden; admin shells via parent-Pi mpremote |
| HTTP server | Same — UI lives on the parent, not the leaf |
| mDNS/UPnP responder | Don't advertise to LAN attackers |
| Arbitrary `exec()` from master | Master can only call configured ops |
| Auto-apply OTA without operator ack | Operator confirms via wizard |

## Adding a new app

1. Drop your app under `app/` in the bundle (Pico filesystem root).
2. Provide `app/main.py` with a `main()` function.
3. Inside `main()`, pull primitives from `neuron.RUNTIME`:

```python
import neuron

def main():
    rt = neuron.RUNTIME
    chans = rt["channels"]
    fr    = rt["failsafe"]
    hwd   = rt["hwdog"]
    led   = rt["led"]

    # Register failsafe handlers for your hardware
    fr.register("estop_pressed", lambda **_: my_disable_motors())

    while True:
        hwd.feed()
        led.tick()
        # ...your loop...
        chans.maybe_heartbeat()
```

4. Master bundles your app + baseline + per-Pico config into one zip.
