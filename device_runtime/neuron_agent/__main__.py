"""Entry point — `python3 -m neuron_agent`.

Boot sequence (mirrors install.sh on the device):
    1. Load + verify bundle from /boot/neuron/   (or --bundle PATH)
    2. Populate device_dna if blank — hash of cpu serial + first MAC.
    3. Apply network_policy (nftables deny-all). Skipped with --no-firewall.
    4. Open MQTT channels to parent. --insecure for dev (plain MQTT).
    5. Start heartbeat loop + telemetry sampler.
    6. Watchdog: if parent heartbeat is missing for
       brain.heartbeat.default_disconnect_grace_seconds, fire interlocks.
    7. Block until SIGTERM / SIGINT — then graceful shutdown.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .bundle import BundleError, load_bundle
from .channels import Channels
from .gpio import open_backend
from .heartbeat import HeartbeatLoop
from .identity import populate_dna_if_blank
from .network_policy import apply as apply_firewall
from .safety import SafetyEngine
from .telemetry import Telemetry


def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="neuron-agent",
                                description="Neuron on-device runtime")
    p.add_argument("--bundle", default="/boot/neuron",
                   help="bundle directory (default /boot/neuron)")
    p.add_argument("--insecure", action="store_true",
                   help="use plain MQTT instead of mTLS (dev only)")
    p.add_argument("--no-firewall", action="store_true",
                   help="skip nftables policy apply (dev only)")
    p.add_argument("--mock-gpio", action="store_true",
                   help="force the mock GPIO backend even on a Pi")
    p.add_argument("--log-level", default="INFO",
                   help="DEBUG / INFO / WARNING / ERROR")
    return p.parse_args()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    args = _cli()
    _setup_logging(args.log_level)
    log = logging.getLogger("neuron_agent")
    log.info("neuron-agent v%s starting", __version__)

    bundle_root = Path(args.bundle)
    try:
        bundle = load_bundle(bundle_root)
    except BundleError as e:
        log.critical("bundle load failed: %s", e)
        return 2
    log.info("bundle ok — dna=%s role=%s asset=%s",
             bundle.device_dna, bundle.role, bundle.asset_id)

    # 2. UUID auto-populate from hardware ID if wizard left it blank.
    dna_path = bundle.root / "dna.json"
    final_dna, was_populated = populate_dna_if_blank(dna_path)
    if was_populated:
        log.info("device_dna populated from hardware: %s", final_dna)

    # 3. Firewall.
    if not args.no_firewall:
        ok = apply_firewall(bundle.brain.get("network_policy"))
        log.info("firewall applied=%s", ok)

    # 4. GPIO backend.
    gpio = open_backend(mock=args.mock_gpio)

    # 5. Channels.
    parent_url = (bundle.brain.get("channels") or {}).get(
        "command_and_control", {}).get("parent_url")
    certs_dir = str(bundle.root / "certs") if (bundle.root / "certs").is_dir() else None
    channels = Channels(final_dna, parent_url, certs_dir=certs_dir,
                        insecure=args.insecure)
    channels.connect()

    # Wire SafetyEngine — it needs publish_alarm + shutdown hooks.
    def _alarm(payload: dict) -> None:
        loc = bundle.dna.get("location") or {}
        topic = "factory/{f}/{l}/{m}/alerts".format(
            f=(loc.get("factory") or "_"),
            l=(loc.get("line") or "_"),
            m=(loc.get("machine") or "_"),
        )
        channels.publish(topic, {"dna": final_dna, **payload}, qos=2)

    def _shutdown() -> None:
        import os
        log.critical("requesting OS shutdown via 'systemctl poweroff'")
        os.system("systemctl poweroff")

    safety = SafetyEngine(bundle.brain, gpio, publish_alarm=_alarm,
                          shutdown=_shutdown)

    # 6. Heartbeat + watchdog.
    hb_conf = bundle.brain.get("heartbeat") or {}
    hb = HeartbeatLoop(
        final_dna,
        interval_ms=int(hb_conf.get("child_to_parent_ms") or 1000),
        publish=channels.publish,
        subscribe=channels.subscribe,
    )
    hb.start()

    grace = int(hb_conf.get("default_disconnect_grace_seconds") or 30)
    parent_alive = True
    stop = threading.Event()

    def _on_signal(*_):
        log.info("signal received — shutting down")
        stop.set()
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # 7. Telemetry sampler.
    tel_conf = bundle.brain.get("telemetry") or {}
    telemetry = Telemetry(tel_conf.get("channels") or [])
    tel_interval = int(tel_conf.get("sample_interval_seconds") or 10)

    # Main loop — tick safety + publish telemetry every tel_interval.
    last_publish = 0.0
    while not stop.is_set():
        readings = telemetry.snapshot()
        safety.evaluate(readings, parent_alive)
        # Watchdog: parent link state.
        gone = hb.seconds_since_parent_heartbeat()
        if parent_alive and gone > grace:
            log.warning("parent silence %.1fs > grace %ds — flipping autonomous",
                        gone, grace)
            safety.enforce_parent_link_lost()
            parent_alive = False
        elif not parent_alive and gone <= grace:
            log.info("parent link restored after %.1fs", gone)
            safety.parent_link_restored()
            parent_alive = True

        now = time.monotonic()
        if now - last_publish >= tel_interval:
            loc = bundle.dna.get("location") or {}
            topic = "factory/{f}/{l}/{m}/telemetry".format(
                f=(loc.get("factory") or "_"),
                l=(loc.get("line") or "_"),
                m=(loc.get("machine") or "_"),
            )
            channels.publish(topic, {"dna": final_dna, **readings}, qos=0)
            last_publish = now
        stop.wait(min(1.0, tel_interval))

    hb.stop()
    channels.disconnect()
    gpio.close()
    log.info("neuron-agent stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
