"""Pico 2 W main loop.

Strategy:
  1. Load DNA + Brain config (they ship together in the firmware bundle).
  2. Bring up the EdgeClient (HTTP shim in this slice).
  3. Send a heartbeat every N seconds. On failure, queue telemetry locally
     and keep running the Brain loops (offline_policy: continue_safe).
  4. Each tick: poll desired-state from Edge, run Brain.step(), report
     reported/current channels back.
  5. Test mode is opt-in via config.json["test_mode"] — runs the manifest
     self-tests once and exits.
"""
import time

try:
    import boot  # noqa: F401  (brings WLAN + CONFIG into scope)
    CONFIG = boot.CONFIG
except Exception:
    CONFIG = {}

import logger
import trace
from device_dna import dna
from heartbeat import Heartbeat
from offline_mode import OfflineQueue
from brain import Brain
from mqtt_client import EdgeClient


def _read_inputs():
    """Stub: real builds wire this to io_drivers/i2c_sensors.read_ds18b20()
    using the pinmap. We expose the hook so test_mode can inject readings."""
    return {}


def run():
    edge_cfg = (CONFIG.get("edge") or {})
    base = edge_cfg.get("url", "http://192.168.1.10:8090")
    period_ms = int(edge_cfg.get("telemetry_period_ms", 1000))
    hb_period = int(edge_cfg.get("heartbeat_period_s", 15))

    client = EdgeClient(base, dna.device_dna)
    hb = Heartbeat(dna, client, period_s=hb_period)
    queue = OfflineQueue(buffer_size=128)
    brain = Brain()

    logger.info("main", "boot", dna=dna.device_dna, fw=dna.base_firmware_version,
                app=dna.app_bundle_version, twin=dna.twin_controls)
    trace.add("boot")

    # Test mode short-circuits to factory checklist.
    if CONFIG.get("test_mode"):
        from test_mode import run as run_tests
        results, all_ok = run_tests([], drivers={})
        logger.info("main", "test_mode complete", ok=all_ok, n=len(results))
        return

    last_tick = 0
    while True:
        now_ms = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.time() * 1000)

        # 1. heartbeat (rate-limited inside Heartbeat)
        hb.maybe_send()

        # 2. fetch desired
        desired = client.get_desired()
        if desired:
            brain.apply_desired(desired)

        # 3. tick brain at telemetry period
        gap = (time.ticks_diff(now_ms, last_tick) if hasattr(time, "ticks_diff")
               else now_ms - last_tick)
        if gap >= period_ms:
            inputs = _read_inputs()
            brain.update_inputs(inputs)
            outputs = brain.step()
            snap = brain.snapshot()

            ok = bool(client.report(snap["reported"]) and client.report_current(snap["current"]))
            if not ok:
                queue.enqueue(snap)
                trace.add("queued_offline", n=len(queue))
            else:
                # Flush any buffered records now that we're back online.
                drained = queue.flush(lambda p: bool(client.report(p.get("reported", {}))))
                if drained:
                    trace.add("flushed", n=drained)

            last_tick = now_ms

        time.sleep_ms(50) if hasattr(time, "sleep_ms") else time.sleep(0.05)


if __name__ == "__main__":
    run()
