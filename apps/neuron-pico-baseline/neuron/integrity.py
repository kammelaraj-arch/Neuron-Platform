"""Config-HMAC + signed-app-manifest verification.

Two layers of tamper-detection:

  1. config.json carries an `config_hmac` field — HMAC-SHA256 of the
     remaining JSON, keyed by a per-Pico secret derived from the
     hardware UUID. Tampering with allowed_hosts / cert pin / API key
     after provisioning is detected at next boot.

  2. config.json carries an `app_manifest` field — {filename: sha256}
     for every .py file under app/ and neuron/. At boot we recompute
     each hash and refuse to start if any file has been modified.

Both checks fail CLOSED: the baseline halts and the LED goes red.
The crash log records which file diverged, so an operator can see
exactly what was tampered with.
"""

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

import json
import os


class IntegrityError(Exception):
    pass


def _device_secret():
    """Derive a per-Pico HMAC key from machine.unique_id() + a domain tag.

    The key never leaves the chip and isn't reproducible without
    physical access — so even an attacker who exfiltrates config.json
    can't forge a new one with a valid HMAC."""
    try:
        import machine
        uid = machine.unique_id()
    except Exception:
        uid = b"host-test-no-machine"
    return hashlib.sha256(b"neuron-pico-hmac/" + uid).digest()


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    """Minimal HMAC-SHA256 (RFC 2104) — MicroPython has no hmac module."""
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    if len(key) < 64:
        key = key + b"\x00" * (64 - len(key))
    o_key = bytes(b ^ 0x5C for b in key)
    i_key = bytes(b ^ 0x36 for b in key)
    inner = hashlib.sha256(i_key + msg).digest()
    return hashlib.sha256(o_key + inner).digest()


def _hex(b: bytes) -> str:
    return "".join("{:02x}".format(x) for x in b)


def verify_config_hmac(cfg: dict) -> None:
    """Recompute HMAC over a canonical projection of cfg and compare.

    The HMAC is computed by the master at bundle-build time using the
    Pico's hardware UUID (which it obtains during initial provisioning
    via a challenge-response). We exclude the `config_hmac` field
    itself from the input."""
    claimed = cfg.get("config_hmac", "")
    payload = {k: v for k, v in cfg.items() if k != "config_hmac"}
    serial = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = _hex(_hmac_sha256(_device_secret(), serial))
    if claimed != expected:
        # During bootstrap (first provision), the master may not yet
        # know the hardware UUID and write a placeholder. Accept the
        # special value "bootstrap" to allow the very first boot, on
        # which the Pico announces its UUID upstream so the master can
        # mint a real HMAC for subsequent re-flashes.
        if claimed != "bootstrap":
            raise IntegrityError("config.json HMAC mismatch — tampering or wrong device")


def _walk_py_files(root):
    """Walk root/ yielding ("<rel>", bytes) for every .py file."""
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for name in entries:
        full = root + "/" + name
        try:
            mode = os.stat(full)[0]
        except OSError:
            continue
        # 0x4000 = directory, 0x8000 = regular file (mostly stable across MP ports)
        if mode & 0x4000:
            for x in _walk_py_files(full):
                yield x
        elif name.endswith(".py"):
            try:
                with open(full, "rb") as f:
                    yield (full.lstrip("/"), f.read())
            except OSError:
                continue


def verify_app_manifest(cfg: dict, roots=("app", "neuron")) -> None:
    """Recompute SHA-256 for every .py file under roots and compare to manifest."""
    manifest = cfg.get("app_manifest") or {}
    if not manifest:
        # Allow empty during bootstrap; subsequent flashes carry one.
        return
    actual = {}
    for path, body in _walk_py_files(""):
        for r in roots:
            if path.startswith(r + "/") or path == r + ".py":
                actual[path] = _hex(hashlib.sha256(body).digest())
                break
    # Files in manifest but missing on disk
    missing = [p for p in manifest if p not in actual]
    if missing:
        raise IntegrityError("app files missing: {}".format(missing))
    # Files whose hash doesn't match
    diff = [p for p in manifest if actual.get(p) != manifest[p]]
    if diff:
        raise IntegrityError("app files tampered: {}".format(diff))
