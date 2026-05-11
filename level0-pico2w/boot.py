"""Pico 2 W boot.py — runs once on power-on before main.py.

Stays small on purpose: read config, optionally wait for a USB host so the
operator has a window to interrupt with Ctrl-C in REPL, and bring up Wi-Fi.
"""
import gc
import json
import time

try:
    import network  # type: ignore
except ImportError:
    network = None


def load_config():
    try:
        with open("config.json") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _connect_wifi(cfg, timeout_s=20):
    if network is None:
        return None
    wifi_cfg = cfg.get("wifi") or {}
    ssid = wifi_cfg.get("ssid")
    password = wifi_cfg.get("password", "")
    if not ssid:
        return None
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan
    wlan.connect(ssid, password)
    deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
    while not wlan.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
        time.sleep_ms(250)
    return wlan


CONFIG = load_config()
WLAN = _connect_wifi(CONFIG)
gc.collect()
print("[boot] dna=", CONFIG.get("device_dna"), " wifi=", "up" if (WLAN and WLAN.isconnected()) else "down")
