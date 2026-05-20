"""Top-level boot.py — Layer 1 baseline boot sequence.

Sequence (FIRST thing that runs on every Pico reset):

  1. Load + validate config.json (fails closed if missing/malformed).
  2. Bring up WiFi from config.wifi (best-effort — UART always works).
  3. Construct identity, host-allow-list, cert-pinner, API client,
     channel manager, watchdog, failsafe-registry — all Layer-1 objects.
  4. Make them importable as `neuron.RUNTIME` for the app layer.
  5. Hand off to `app.main` if it exists, else stop with a flashing LED.

The app layer never touches the network primitives directly. It pulls
ChannelManager etc. from `neuron.RUNTIME` and calls public methods.
This keeps the host allow-list + cert pin + API key in exactly one
place (Layer 1), enforced for every outbound byte.
"""
import sys
import time

import neuron
from neuron import (
    load_config, Identity, NeuronAPI, HostAllowList, CertPinner,
    ChannelManager, ParentWatchdog, FailsafeRegistry,
)

try:
    import network
except ImportError:
    network = None


def _bring_up_wifi(cfg):
    w = (cfg.get("wifi") or {})
    if network is None or not w.get("ssid"):
        print("[boot] WiFi skipped (no network module or no SSID)")
        return False
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if w.get("country_code"):
        try:
            network.country(w["country_code"])
        except Exception:
            pass
    print("[boot] WiFi connecting to", w["ssid"])
    wlan.connect(w["ssid"], w.get("password") or "")
    for _ in range(40):  # ~20s
        if wlan.isconnected():
            print("[boot] WiFi OK:", wlan.ifconfig())
            return True
        time.sleep(0.5)
    print("[boot] WiFi timeout — continuing in UART-only mode")
    return False


def _bootstrap():
    cfg = load_config("config.json")
    print("[boot] schema_version:", cfg["schema_version"])
    print("[boot] device DNA:", cfg["device_dna"])
    print("[boot] allow_hosts:", cfg["allowed_hosts"])

    wifi_ok = _bring_up_wifi(cfg)

    identity = Identity(cfg)
    allow    = HostAllowList(cfg["allowed_hosts"])
    pinner   = CertPinner(cfg["tls_cert_sha256"])
    api      = NeuronAPI(cfg, allow, pinner)
    fr       = FailsafeRegistry()
    wdog     = ParentWatchdog(cfg.get("brain"), fr)
    chans    = ChannelManager(api, identity, cfg.get("brain"))

    # Expose to the app layer.
    neuron.RUNTIME = {
        "config":   cfg,
        "identity": identity,
        "allow":    allow,
        "pinner":   pinner,
        "api":      api,
        "channels": chans,
        "watchdog": wdog,
        "failsafe": fr,
        "wifi_ok":  wifi_ok,
    }

    print("[boot] Layer 1 ready, DNA=", identity.dna)


def _handoff_to_app():
    try:
        import app.main as _app   # type: ignore[import-not-found]
        if hasattr(_app, "main"):
            _app.main()
        # else: importing already ran whatever the app needed
    except ImportError:
        print("[boot] app layer not present — baseline-only mode")


def _panic():
    """Slow-blink the onboard LED forever to indicate boot failure."""
    try:
        from machine import Pin
        led = Pin("LED", Pin.OUT)
        while True:
            led.value(not led.value())
            time.sleep(1)
    except Exception:
        sys.exit(1)


try:
    _bootstrap()
    _handoff_to_app()
except Exception as e:
    sys.print_exception(e) if hasattr(sys, "print_exception") else print(e)
    _panic()
