"""config.json loader — fails noisily if required keys are missing.

The bundle generator on the master writes this file fresh per Pico.
Required schema (validated on every boot):

  {
    "schema_version": "1",
    "device_dna":    "pico-…",
    "group_id":      "<uuid>",
    "api_key":       "neu_…",         # per-Pico, scope=pico
    "allowed_hosts": ["neuron.shital.org.uk"],
    "tls_cert_sha256": "AB:CD:…",     # pin the master's cert fingerprint
    "wifi":     {"ssid": …, "password": …, "country_code": …},
    "brain":    { … },
    "channels": { control, emergency, ota, uart_local }
  }
"""
import json


REQUIRED_KEYS = (
    "schema_version", "device_dna", "group_id",
    "api_key", "allowed_hosts", "tls_cert_sha256",
    "channels",
)


class ConfigError(Exception):
    pass


def load_config(path="config.json"):
    try:
        with open(path, "r") as f:
            cfg = json.load(f)
    except OSError as e:
        raise ConfigError("config.json not found — Pico not provisioned") from e
    except ValueError as e:
        raise ConfigError(f"config.json invalid JSON: {e}") from e
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ConfigError(f"config.json missing keys: {missing}")
    if not isinstance(cfg["allowed_hosts"], list) or not cfg["allowed_hosts"]:
        raise ConfigError("allowed_hosts must be a non-empty list")
    return cfg
