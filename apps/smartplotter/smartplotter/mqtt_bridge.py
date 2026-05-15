"""MQTT bridge — publishes telemetry to the master's broker AND
subscribes to the emergency channel + parent heartbeat. Treats
SafetyMonitor as the single ground truth (drives `trigger_safe_stop`
on emergency receive, calls `heartbeat_from_parent` on heartbeat
receive).

mTLS-first: when certs_dir/{ca,device}.crt + device.key exist, opens
mqtts:// with client cert auth. Falls back to plain MQTT only when
certs are missing AND broker_url is mqtt:// (operator opted in).
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config


log = logging.getLogger("smartplotter.mqtt")


class MqttBridge:
    def __init__(self, settings, safety):
        self.s = settings
        self.safety = safety
        self._client = None
        self._connected = False
        self._stop = threading.Event()
        self._telemetry_thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.s.broker_url:
            log.warning("MQTT broker_url empty — telemetry + emergency channel disabled")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.error("paho-mqtt not installed — MQTT disabled")
            return

        u = urlparse(self.s.broker_url)
        host = u.hostname or "localhost"
        port = u.port or (8883 if u.scheme in ("mqtts", "ssl") else 1883)
        client = mqtt.Client(client_id=f"smartplotter-{self.s.device_dna}",
                             clean_session=True, protocol=mqtt.MQTTv311)
        certs = self.s.certs_dir
        if certs.is_dir() and (certs / "device.crt").is_file():
            client.tls_set(
                ca_certs=str(certs / "ca.crt"),
                certfile=str(certs / "device.crt"),
                keyfile=str(certs / "device.key"),
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            client.tls_insecure_set(False)
            log.info("MQTT mTLS configured from %s", certs)
        elif u.scheme not in ("mqtt",):
            log.error("certs missing at %s — refusing to connect over %s without mTLS",
                      certs, u.scheme)
            return

        client.on_connect    = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message    = self._on_message
        try:
            client.connect_async(host, port, keepalive=30)
            client.loop_start()
            self._client = client
            log.info("MQTT connecting to %s:%d", host, port)
        except Exception as e:
            log.error("MQTT connect failed: %s", e)
            return

        # Background telemetry publisher (every 5s)
        self._telemetry_thread = threading.Thread(
            target=self._telemetry_loop, daemon=True, name="smartplotter-telemetry")
        self._telemetry_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass

    # ── paho callbacks ─────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            log.error("MQTT connect rc=%s", rc); return
        self._connected = True
        log.info("MQTT connected")
        client.subscribe(config.topic_emergency(self.s), qos=2)
        client.subscribe(f"heartbeat/{self.s.device_dna}/down", qos=1)

    def _on_disconnect(self, client, userdata, rc):
        log.warning("MQTT disconnected rc=%s", rc)
        self._connected = False

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8")) if msg.payload else {}
        except Exception:
            payload = {}
        if topic == config.topic_emergency(self.s):
            cmd = (payload.get("command") or "").lower()
            log.warning("emergency channel: %s payload=%s", cmd, payload)
            if cmd == "safe_stop":
                self.safety.trigger_safe_stop("master_safe_stop")
            elif cmd == "safe_shutdown":
                self.safety.trigger_safe_stop("master_safe_shutdown")
                import os
                os.system("systemctl poweroff")
            elif cmd == "status":
                self._publish_status()
        elif topic.endswith("/down"):
            self.safety.heartbeat_from_parent()

    # ── Publishers ─────────────────────────────────────────────────────
    def _publish_status(self) -> None:
        if not (self._client and self._connected):
            return
        self._client.publish(
            f"twin/{self.s.device_dna}/reported",
            json.dumps({"dna": self.s.device_dna,
                        "safety": self.safety.state.__dict__,
                        "ts": int(time.time())}),
            qos=1)

    def _telemetry_loop(self) -> None:
        from .telemetry import sample as telemetry_sample
        while not self._stop.is_set():
            if self._client and self._connected:
                snap = telemetry_sample()
                snap["dna"] = self.s.device_dna
                snap["is_safe"] = self.safety.state.is_safe
                snap["fault"] = self.safety.state.fault
                try:
                    self._client.publish(
                        config.topic_telemetry(self.s),
                        json.dumps(snap), qos=0)
                except Exception as e:
                    log.debug("telemetry publish failed: %s", e)
            self._stop.wait(5.0)

    def publish_event(self, kind: str, payload: dict) -> None:
        """Fire-and-forget event (recipe started / ended, alarm raised)."""
        if not (self._client and self._connected):
            return
        try:
            self._client.publish(
                config.topic_alerts(self.s),
                json.dumps({"kind": kind, "ts": int(time.time()), **payload}),
                qos=1)
        except Exception:
            pass
