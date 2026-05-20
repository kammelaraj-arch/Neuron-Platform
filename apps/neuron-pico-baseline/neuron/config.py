"""config.json loader with required-key validation.

The bundle generator on the master writes this file fresh per Pico.
Fails CLOSED on any inconsistency — refuses to boot rather than run
with a partial/corrupt config (much better than silently using stale
or default values for security-relevant fields like allowed_hosts).
"""
import json


REQUIRED_KEYS = (
    "schema_version",
    "device_dna",
    "group_id",
    "api_key",
    "allowed_hosts",
    "tls_cert_sha256",
    "config_hmac",       # HMAC-SHA256 over the rest of the file
    "app_manifest",      # {filename: sha256_hex} for every app .py file
    "channels",
)


class ConfigError(Exception):
    pass


def load_config(path="config.json"):
    try:
        with open(path, "r") as f:
            cfg = json.load(f)
    except OSError as e:
        raise ConfigError("config.json missing — Pico not provisioned") from e
    except ValueError as e:
        raise ConfigError("config.json invalid JSON: {}".format(e)) from e

    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ConfigError("config.json missing required keys: {}".format(missing))

    if not isinstance(cfg["allowed_hosts"], list) or not cfg["allowed_hosts"]:
        raise ConfigError("allowed_hosts must be a non-empty list")
    if not isinstance(cfg["channels"], dict):
        raise ConfigError("channels must be a dict")
    for ch_name in ("control", "emergency", "ota", "uart_local"):
        if ch_name not in cfg["channels"]:
            raise ConfigError("channels.{} missing — default-channel contract violated".format(ch_name))

    if cfg.get("schema_version") != "1":
        raise ConfigError("unsupported config schema_version: {}".format(cfg.get("schema_version")))

    return cfg
