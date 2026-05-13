"""MQTT channels — command-and-control + emergency + telemetry.

Wraps paho-mqtt with the bit we actually need:
    • outbound-only connect to parent broker (parent.parent_url)
    • optional mTLS (cert paths from /boot/neuron/certs/ when present;
      `--insecure` for dev)
    • publish JSON helper
    • subscribe(topic, callback)

Designed so other modules (heartbeat, safety, telemetry) take a small
publish/subscribe pair and don't import paho directly.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
from typing import Callable
from urllib.parse import urlparse


log = logging.getLogger("neuron_agent.channels")


class _NoMqtt(Exception):
    pass


class Channels:
    def __init__(
        self,
        device_dna: str,
        parent_url: str | None,
        certs_dir: str | None = None,
        insecure: bool = False,
    ) -> None:
        self.device_dna = device_dna
        self.parent_url = parent_url
        self.certs_dir = certs_dir
        self.insecure = insecure
        self._client = None
        self._subs: dict[str, Callable[[bytes], None]] = {}
        self._lock = threading.Lock()

    def connect(self) -> None:
        if not self.parent_url:
            log.warning("no parent_url in brain.json — channels disabled")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise _NoMqtt(f"paho-mqtt not installed: {e}") from e

        u = urlparse(self.parent_url if "://" in self.parent_url
                     else f"mqtts://{self.parent_url}")
        host = u.hostname or "localhost"
        # mqtts (mTLS) → 8883, mqtt → 1883.
        port = u.port or (8883 if u.scheme in ("mqtts", "ssl") else 1883)

        client = mqtt.Client(client_id=self.device_dna, clean_session=True,
                             protocol=mqtt.MQTTv311)
        if not self.insecure and self.certs_dir:
            import pathlib
            d = pathlib.Path(self.certs_dir)
            client.tls_set(
                ca_certs=str(d / "ca.crt"),
                certfile=str(d / "device.crt"),
                keyfile=str(d / "device.key"),
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            client.tls_insecure_set(False)
        elif not self.insecure:
            log.error("no certs_dir + not --insecure → refusing to publish in plain mode")
            return

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
        self._client = client
        log.info("MQTT connecting to %s:%d (tls=%s)", host, port, not self.insecure)

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    # ── publish / subscribe helpers ────────────────────────────────────
    def publish(self, topic: str, payload: dict, qos: int = 1) -> None:
        if self._client is None:
            log.debug("MQTT down — would have published to %s: %r", topic, payload)
            return
        self._client.publish(topic, json.dumps(payload, separators=(",", ":")),
                             qos=qos, retain=False)

    def subscribe(self, topic: str, cb: Callable[[bytes], None]) -> None:
        with self._lock:
            self._subs[topic] = cb
        if self._client is not None:
            self._client.subscribe(topic, qos=1)

    # ── paho callbacks ─────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc):
        log.info("MQTT connected rc=%s", rc)
        # Re-subscribe everything in case this is a reconnect.
        with self._lock:
            for topic in self._subs:
                client.subscribe(topic, qos=1)

    def _on_message(self, client, userdata, msg):
        cb = self._subs.get(msg.topic)
        if cb is None:
            log.debug("MQTT unsolicited topic %s", msg.topic)
            return
        try:
            cb(msg.payload)
        except Exception as e:
            log.error("subscriber for %s threw: %s", msg.topic, e)
