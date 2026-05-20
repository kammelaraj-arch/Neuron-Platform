"""Neuron Pico 2 W production baseline (Layer 1).

This is the canonical platform layer every Pico-based app sits on top of.
Provides — in one self-contained package:

  Identity & versioning   identity.py
  Security (allow-list,
   cert-pin, HMAC, signed app) security.py, integrity.py
  Per-Pico API client     api.py
  4-channel comms         channels.py
  Parent watchdog         watchdog.py
  Failsafe registry       failsafe.py
  Hardware watchdog       hwdog.py
  LED state codes         led.py
  Crash log persistence   crashlog.py
  WiFi auto-reconnect     wifi.py
  NTP time sync           ntp.py
  Telemetry batching      telemetry.py

Hard rules enforced by this layer (per CLAUDE.md):
  - Parent-only communication: no inbound listening services.
  - Default-safe boot: motors disabled at every reset.
  - 4 channels always: control + emergency + OTA + UART (local).
  - State changes auto-push to parent.
  - Bidirectional heartbeat (parent → child watchdog drops trip failsafe).
"""
from .config   import load_config
from .identity import Identity
from .security import HostAllowList, CertPinner
from .integrity import verify_app_manifest, verify_config_hmac
from .api      import NeuronAPI
from .channels import ChannelManager
from .watchdog import ParentWatchdog
from .hwdog    import HardwareWatchdog
from .failsafe import FailsafeRegistry
from .led      import StatusLED, STATE_BOOT, STATE_OK, STATE_DEGRADED, STATE_FAILSAFE, STATE_FAULT
from .crashlog import CrashLog
from .wifi     import WiFiManager
from .ntp      import sync_time
from .telemetry import TelemetryBatcher

__version__ = "1.0.0"

# Populated by boot.py once the Layer-1 graph is wired. Apps read this.
RUNTIME = None

__all__ = [
    "load_config", "Identity",
    "HostAllowList", "CertPinner",
    "verify_app_manifest", "verify_config_hmac",
    "NeuronAPI", "ChannelManager",
    "ParentWatchdog", "HardwareWatchdog",
    "FailsafeRegistry",
    "StatusLED", "STATE_BOOT", "STATE_OK", "STATE_DEGRADED", "STATE_FAILSAFE", "STATE_FAULT",
    "CrashLog", "WiFiManager", "sync_time", "TelemetryBatcher",
    "RUNTIME", "__version__",
]
