"""Pico 2 W Layer-1 baseline boot — runs FIRST at every reset.

Sequence (each step fails CLOSED — boot halts and LED → STATE_FAULT):

  1. Status LED on, STATE_BOOT (operator sees activity)
  2. CrashLog.drain — collect any pending traceback from last run
  3. load_config + verify HMAC + verify app manifest
  4. Construct identity (DNA, hardware UUID, reset cause)
  5. Bring up WiFi (best-effort — UART always works)
  6. NTP sync (best-effort, after WiFi)
  7. Construct HostAllowList, CertPinner, NeuronAPI
  8. Construct FailsafeRegistry (passing crash log for handler errors)
  9. Construct ParentWatchdog, HardwareWatchdog, ChannelManager,
     TelemetryBatcher
  10. Ship the pending crash log + reset cause + announce upstream
  11. Expose all of these on neuron.RUNTIME so the app layer can use them
  12. Hand off to app.main()

The app layer (app/main.py) NEVER touches the network primitives
directly. It pulls ChannelManager / FailsafeRegistry / etc. from
neuron.RUNTIME and calls public methods. The host-allow-list, cert
pin, API key, watchdog, integrity check, and failsafe contract are
enforced in exactly one place — here — for every Pico-based app.
"""
import gc
import sys
import time

import neuron
from neuron import (
    load_config, Identity,
    HostAllowList, CertPinner,
    verify_app_manifest, verify_config_hmac,
    NeuronAPI, ChannelManager,
    ParentWatchdog, HardwareWatchdog,
    FailsafeRegistry,
    StatusLED, STATE_BOOT, STATE_OK, STATE_DEGRADED, STATE_FAILSAFE, STATE_FAULT,
    CrashLog, WiFiManager, sync_time, TelemetryBatcher,
)


def _panic_loop(led, crash_log, kind, exc=None):
    """Boot failed — record + flash SOS forever. Hardware watchdog will
    reset the chip after timeout if we ever get here, so we'll retry
    the boot in 8s. That auto-retry handles transient failures (e.g.
    flash filesystem just-mounted not ready) without operator action."""
    try:
        crash_log.record(kind, exc=exc)
    except Exception:
        pass
    led.set(STATE_FAULT)
    while True:
        led.tick()
        time.sleep_ms(50) if hasattr(time, "sleep_ms") else time.sleep(0.05)


def _main():
    led = StatusLED()
    led.set(STATE_BOOT)
    led.tick()

    crash_log = CrashLog()
    pending_crashes = crash_log.drain()  # consume + clear

    # ─── 1) Config + integrity ───────────────────────────────────────
    try:
        cfg = load_config("config.json")
        verify_config_hmac(cfg)
        verify_app_manifest(cfg)
    except Exception as e:
        _panic_loop(led, crash_log, "config_or_integrity_failure", exc=e)

    print("[boot] DNA:", cfg["device_dna"])
    print("[boot] allowed_hosts:", cfg["allowed_hosts"])
    print("[boot] integrity OK")

    # ─── 2) Identity ────────────────────────────────────────────────
    identity = Identity(cfg)
    print("[boot] last reset cause:", identity.last_reset_cause)

    # ─── 3) WiFi + NTP (best-effort) ────────────────────────────────
    wifi = WiFiManager(cfg.get("wifi"))
    wifi_ok = wifi.ensure_connected()
    if wifi_ok:
        print("[boot] WiFi up:", wifi.ifconfig)
        sync_time()
    else:
        print("[boot] WiFi not connected — UART-only mode")

    # ─── 4) Security objects ────────────────────────────────────────
    allow  = HostAllowList(cfg["allowed_hosts"])
    pinner = CertPinner(cfg["tls_cert_sha256"])
    api    = NeuronAPI(cfg, allow, pinner)

    # ─── 5) Safety wiring ───────────────────────────────────────────
    fr   = FailsafeRegistry(crash_log=crash_log)
    wdog = ParentWatchdog(cfg.get("brain"), fr)
    hwd  = HardwareWatchdog(timeout_ms=8000)
    hwd.start()

    chans = ChannelManager(api, identity, cfg.get("brain"))
    tb    = TelemetryBatcher(chans)

    # ─── 6) Ship pending crashes from last run ─────────────────────
    if pending_crashes:
        print("[boot] shipping", len(pending_crashes), "pending crash entries")
        for c in pending_crashes:
            chans.publish_emergency("crash_log", c)

    # Announce reset cause + identity (replay on every boot — idempotent)
    chans.publish_heartbeat({"boot_announce": True,
                             "identity": identity.as_dict()})

    # ─── 7) Expose runtime to app layer ─────────────────────────────
    neuron.RUNTIME = {
        "config":     cfg,
        "identity":   identity,
        "allow":      allow,
        "pinner":     pinner,
        "api":        api,
        "channels":   chans,
        "telemetry":  tb,
        "failsafe":   fr,
        "watchdog":   wdog,
        "hwdog":      hwd,
        "led":        led,
        "wifi":       wifi,
        "crashlog":   crash_log,
        "wifi_ok":    wifi_ok,
    }
    led.set(STATE_OK if wifi_ok else STATE_DEGRADED)

    # Standard failsafe wiring for any app that doesn't override
    def _on_link_lost(**kw):
        led.set(STATE_FAILSAFE)
        chans.publish_emergency("parent_link_lost", kw)

    def _on_link_restored(**kw):
        led.set(STATE_OK if wifi.connected else STATE_DEGRADED)

    fr.register("parent_link_lost", _on_link_lost)
    fr.register("parent_link_restored", _on_link_restored)

    print("[boot] Layer 1 ready — handing off to app")

    # ─── 8) Hand off to app ────────────────────────────────────────
    try:
        import app.main as _app  # type: ignore[import-not-found]
        if hasattr(_app, "main"):
            _app.main()
        # If app.main just does its work at import time, we're done.
    except ImportError:
        print("[boot] app layer not present — baseline-only mode")
        # Idle loop so the watchdog gets fed and LED stays alive.
        while True:
            hwd.feed()
            led.tick()
            wifi.ensure_connected()
            chans.maybe_heartbeat()
            chans.maybe_poll_ota()
            wdog.tick()
            gc.collect()
            time.sleep_ms(50) if hasattr(time, "sleep_ms") else time.sleep(0.05)
    except Exception as e:
        crash_log.record("uncaught_exception_in_app", exc=e)
        _panic_loop(led, crash_log, "app_crashed", exc=e)


# Single try around _main so a Python-level error during boot still
# gets to the panic loop (LED + crash log + auto-reset via WDT).
try:
    _main()
except Exception as _e:
    try:
        sys.print_exception(_e) if hasattr(sys, "print_exception") else print(_e)
    except Exception:
        pass
    # Last-ditch panic without the constructed led/crashlog objects.
    while True:
        time.sleep(1)
