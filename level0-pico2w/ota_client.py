"""OTA client — pulls a signed firmware bundle from the Master and stages
it on flash. Enforces (locally, before any apply):

  * signature verifies against the embedded master public key
  * supported_hardware_revisions includes our hardware_revision
  * min_supported_base_firmware_version <= our base_firmware_version
  * downgrade refused unless rollback_allowed is set on a re-signed manifest

The actual swap happens at next reboot via main.py's startup hook.
"""
import binascii
import hashlib
import json

import logger
import trace
import ed25519_verify

try:
    import urequests as requests  # type: ignore
except ImportError:
    try:
        import requests  # type: ignore
    except ImportError:
        requests = None  # type: ignore


PUBKEY_PATH = "ota_pubkey.txt"  # base64-encoded master Ed25519 public key


def _semver_ge(a, b):
    pa = [int(x) for x in a.split("-")[0].split(".") if x.isdigit()]
    pb = [int(x) for x in b.split("-")[0].split(".") if x.isdigit()]
    while len(pa) < 3: pa.append(0)
    while len(pb) < 3: pb.append(0)
    return pa >= pb


def _semver_gt(a, b):
    pa = [int(x) for x in a.split("-")[0].split(".") if x.isdigit()]
    pb = [int(x) for x in b.split("-")[0].split(".") if x.isdigit()]
    while len(pa) < 3: pa.append(0)
    while len(pb) < 3: pb.append(0)
    return pa > pb


def _load_pubkey():
    try:
        with open(PUBKEY_PATH) as fh:
            data = fh.read().strip()
        return binascii.a2b_base64(data)
    except Exception:
        return None


def _canonical(obj):
    """RFC 8259 minimal canonical JSON: sorted keys, no spaces."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_manifest(manifest, pubkey=None):
    pub = pubkey if pubkey is not None else _load_pubkey()
    if pub is None:
        return False, "no public key on device"
    sig = manifest.get("signature")
    if not isinstance(sig, dict) or sig.get("alg") != "ed25519" or not sig.get("value"):
        return False, "manifest is not Ed25519-signed"
    body = dict(manifest)
    sig_b64 = body.pop("signature")["value"]
    canon = _canonical(body)
    try:
        sig_bytes = binascii.a2b_base64(sig_b64)
    except Exception:
        return False, "signature not base64"
    ok = ed25519_verify.verify(pub, canon, sig_bytes)
    return ok, "signature ok" if ok else "signature mismatch"


def check_compatibility(manifest, dna):
    if dna.hardware_revision not in manifest.get("supported_hardware_revisions", []):
        return False, "hardware_revision %s not supported" % dna.hardware_revision
    min_base = manifest.get("min_supported_base_firmware_version", "0.0.0")
    if not _semver_ge(dna.base_firmware_version, min_base):
        return False, "base_firmware_version %s < min %s" % (dna.base_firmware_version, min_base)
    target = manifest.get("target_app_bundle_version", "0.0.0")
    current = dna.app_bundle_version
    if _semver_gt(current, target) and not manifest.get("rollback_allowed"):
        return False, "downgrade %s -> %s requires rollback_allowed" % (current, target)
    return True, "ok"


def fetch_manifest(base_url, device_dna, channel="stable", timeout_s=8):
    if requests is None:
        return None, "no http client"
    url = "%s/api/devices/%s/firmware-manifest?channel=%s" % (base_url, device_dna, channel)
    try:
        resp = requests.get(url, timeout=timeout_s)
        try:
            if resp.status_code != 200:
                return None, "status %d" % resp.status_code
            return json.loads(resp.text), "ok"
        finally:
            try: resp.close()
            except Exception: pass
    except Exception as exc:
        return None, "manifest fetch failed: %s" % exc


def download_bundle(base_url, device_dna, channel, expected_sha256, dest_path="bundle.zip", timeout_s=30):
    if requests is None:
        return False, "no http client"
    url = "%s/api/devices/%s/firmware-bundle?channel=%s" % (base_url, device_dna, channel)
    try:
        resp = requests.get(url, timeout=timeout_s)
        try:
            if resp.status_code != 200:
                return False, "status %d" % resp.status_code
            data = resp.content
        finally:
            try: resp.close()
            except Exception: pass
    except Exception as exc:
        return False, "download failed: %s" % exc

    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        return False, "bundle sha256 mismatch (got %s)" % digest
    try:
        with open(dest_path, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        return False, "write failed: %s" % exc
    return True, dest_path


def stage_apply(bundle_path, manifest, dna):
    """Validate signature + compatibility, write a flag file the boot path
    consumes on next power-cycle."""
    ok, reason = verify_manifest(manifest)
    if not ok:
        trace.add("ota_sig_rejected", reason=reason)
        logger.warn("ota_client", "rejected", reason=reason)
        return False, reason
    ok, reason = check_compatibility(manifest, dna)
    if not ok:
        trace.add("ota_compat_rejected", reason=reason)
        logger.warn("ota_client", "rejected", reason=reason)
        return False, reason
    try:
        with open("ota.staged", "w") as fh:
            fh.write(json.dumps({"bundle": bundle_path, "manifest": manifest}))
    except OSError as exc:
        return False, str(exc)
    trace.add("ota_staged", target=manifest.get("target_app_bundle_version"))
    logger.info("ota_client", "staged", target=manifest.get("target_app_bundle_version"))
    return True, "staged"


def run_check(base_url, device_dna, dna, channel="stable"):
    """Convenience entry point for main loop: fetch manifest → if a newer
    target, download + verify + stage."""
    manifest, why = fetch_manifest(base_url, device_dna, channel)
    if manifest is None:
        return False, why
    target = manifest.get("target_app_bundle_version", "0.0.0")
    if not _semver_gt(target, dna.app_bundle_version):
        if not (manifest.get("rollback_allowed") and target != dna.app_bundle_version):
            return False, "no newer release on channel %s" % channel
    expected_sha = ""
    for art in manifest.get("artifacts", []) or []:
        if art.get("path") == "manifest.json":
            continue
    bundle_ok, bundle_path = download_bundle(base_url, device_dna, channel, expected_sha)
    if not bundle_ok:
        return False, bundle_path
    return stage_apply(bundle_path, manifest, dna)
