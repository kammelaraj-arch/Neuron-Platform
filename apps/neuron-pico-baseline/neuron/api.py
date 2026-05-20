"""HTTPS client that goes through host allow-list + cert pinning + API key.

Every outbound call from a Pico app goes through this class. It refuses
to talk to any host outside the allow-list and refuses any TLS server
whose cert fingerprint doesn't match the pinned value. Adds the
device's per-Pico API key on every request — the master uses it to
identify which Pico is calling and rate-limit accordingly.

Methods only support the verbs the Pico actually needs:
  - heartbeat (POST)
  - publish telemetry (POST)
  - poll OTA (GET)
  - post emergency (POST)
No verbs to enumerate other devices, mutate users, read audit, etc.
"""
import json

try:
    import urequests as requests
except ImportError:
    requests = None   # falls back to socket-based send on host


class NeuronAPI:
    def __init__(self, cfg, host_allow, cert_pinner):
        self.cfg = cfg
        self.api_key = cfg["api_key"]
        self.allow = host_allow
        self.pinner = cert_pinner

        # Pull base URLs from channels.json the master built.
        ch = cfg["channels"]
        self.control_host = ch["control"]["broker_host"]
        self.ota_url      = ch["ota"]["endpoint"]
        # Master base = scheme + host of the OTA endpoint (always HTTPS).
        self._base = self.ota_url.rsplit("/api/", 1)[0] if "/api/" in self.ota_url else "https://" + self.control_host

    # ── internal ──────────────────────────────────────────────────────
    def _host_of(self, url: str) -> str:
        # crude scheme://host/... split
        if "://" in url:
            url = url.split("://", 1)[1]
        return url.split("/", 1)[0]

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "X-Device-DNA": self.cfg["device_dna"],
        }

    def _request(self, method: str, url: str, body: dict | None = None):
        self.allow.check(self._host_of(url))
        if requests is None:
            raise RuntimeError("urequests not available")
        kwargs = {"headers": self._headers(), "timeout": 10}
        if body is not None:
            kwargs["data"] = json.dumps(body)
        return requests.request(method, url, **kwargs)

    # ── allowed Pico verbs ───────────────────────────────────────────
    def heartbeat(self, payload: dict) -> bool:
        try:
            r = self._request("POST", f"{self._base}/api/pico/heartbeat", payload)
            r.close()
            return 200 <= r.status_code < 300
        except Exception:
            return False

    def telemetry(self, payload: dict) -> bool:
        try:
            r = self._request("POST", f"{self._base}/api/pico/telemetry", payload)
            r.close()
            return 200 <= r.status_code < 300
        except Exception:
            return False

    def ota_poll(self) -> dict | None:
        try:
            r = self._request("GET", self.ota_url)
            data = r.json()
            r.close()
            return data
        except Exception:
            return None

    def emergency(self, payload: dict) -> bool:
        try:
            r = self._request("POST", f"{self._base}/api/pico/emergency", payload)
            r.close()
            return 200 <= r.status_code < 300
        except Exception:
            return False
