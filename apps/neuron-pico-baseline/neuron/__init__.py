"""Neuron Pico 2 W baseline image.

This package is the **Layer 1** firmware — the platform's baseline that
every Pico-based app sits on top of. It encapsulates:

  - Identity: device DNA, group ID, hardware UUID, build version
  - Security: API-key auth, host allow-list, TLS cert pinning
  - Communication channels: control (mTLS), emergency (mTLS), OTA (HTTPS),
    local UART parent link — all four spec'd in CLAUDE.md.
  - Watchdog: parent-link heartbeat with autonomous failsafe on loss
  - Failsafe contract: apps register handlers; baseline enforces them
    when comms degrade, regardless of app behaviour.

App layers (e.g. SmartPlotter) import `neuron.*` and never reach the
network directly. This keeps the security perimeter — host allow-list,
cert pinning, API key scopes — in one place and not duplicated per app.

A "Neuron-blessed" Pico is one that has:
  1. MicroPython runtime (Layer 0)
  2. This `neuron/` package (Layer 1)
  3. An `app/` package with main() (Layer 2)
  4. config.json with identity + creds + endpoint allow-list

The baseline refuses to operate without all four.
"""
from .config import load_config
from .identity import Identity
from .api import NeuronAPI
from .security import HostAllowList, CertPinner
from .channels import ChannelManager
from .watchdog import ParentWatchdog
from .failsafe import FailsafeRegistry

__version__ = "1.0.0"
__all__ = ["load_config", "Identity", "NeuronAPI", "HostAllowList",
           "CertPinner", "ChannelManager", "ParentWatchdog",
           "FailsafeRegistry", "__version__"]
