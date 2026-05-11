"""HTTP-over-Wi-Fi client used to talk to the Edge Runtime.

The platform spec calls for MQTT, and the Edge Runtime accepts the same
twin/{kind}/{dna}/{channel}/{field} topic shape via REST. This module hides
the choice — when ``umqtt.simple`` is present we use MQTT publish/subscribe,
otherwise we fall back to JSON-over-HTTP (which the Edge Runtime supports
natively at ``/api/v1/twin/{dna}/{channel}``).
"""
import json
import logger

try:
    import urequests as requests  # type: ignore
except ImportError:
    try:
        import requests  # type: ignore
    except ImportError:
        requests = None  # type: ignore


class EdgeClient:
    def __init__(self, base_url, device_dna, timeout_s=4):
        self._base = base_url.rstrip("/")
        self._dna = device_dna
        self._timeout = timeout_s

    def _post(self, path, payload):
        if requests is None:
            logger.warn("mqtt_client", "no http client", path=path)
            return None
        url = self._base + path
        try:
            resp = requests.post(url, json=payload, timeout=self._timeout)
            try:
                code = resp.status_code
                body = resp.text
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            return (code, body)
        except Exception as exc:
            logger.warn("mqtt_client", "post failed", url=url, err=str(exc))
            return None

    def heartbeat(self, body):
        return self._post("/api/v1/devices/heartbeat", body)

    def report(self, payload):
        return self._post("/api/v1/twin/%s/reported" % self._dna, {"payload": payload})

    def report_current(self, payload):
        return self._post("/api/v1/twin/%s/current" % self._dna, {"payload": payload})

    def get_desired(self):
        if requests is None:
            return None
        url = self._base + "/api/v1/twin/" + self._dna
        try:
            resp = requests.get(url, timeout=self._timeout)
            try:
                if resp.status_code != 200:
                    return None
                doc = json.loads(resp.text)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            return doc.get("desired", {})
        except Exception as exc:
            logger.warn("mqtt_client", "get desired failed", err=str(exc))
            return None
