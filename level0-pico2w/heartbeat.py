"""Periodic heartbeat → Edge. Carries identity + the version triplet so the
Edge / Master can decide whether OTA gates pass.
"""
import time

import logger
import trace


class Heartbeat:
    def __init__(self, dna, client, period_s=15):
        self._dna = dna
        self._client = client
        self._period_ms = period_s * 1000
        self._next_at = 0
        self._sent = 0
        self._failed = 0

    def maybe_send(self, force=False):
        now = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.time() * 1000)
        if not force and now < self._next_at:
            return False
        body = {
            "device_dna": self._dna.device_dna,
            "device_type": self._dna.device_type,
            "compute_stable_id": self._dna.compute_stable_id,
            "hardware_revision": self._dna.hardware_revision,
            "base_firmware_version": self._dna.base_firmware_version,
            "app_bundle_version": self._dna.app_bundle_version,
            "config_schema_version": self._dna.config_schema_version,
            "twin_controls": self._dna.twin_controls,
        }
        result = self._client.heartbeat(body)
        ok = bool(result and result[0] == 200)
        if ok:
            self._sent += 1
            trace.add("heartbeat_ok")
        else:
            self._failed += 1
            trace.add("heartbeat_fail")
            logger.warn("heartbeat", "edge unreachable", failed=self._failed)
        if hasattr(time, "ticks_add"):
            self._next_at = time.ticks_add(time.ticks_ms(), self._period_ms)
        else:
            self._next_at = now + self._period_ms
        return ok

    def stats(self):
        return {"sent": self._sent, "failed": self._failed}
