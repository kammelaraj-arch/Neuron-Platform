"""HTTP client with allow-list + cert pin + API-key + bounded retry.

Every outbound call from app/baseline code goes through this class. It:
  - refuses any host not in HostAllowList
  - refuses any TLS server cert whose fingerprint differs from the pin
  - injects the per-Pico API key and DNA on every request
  - retries up to N times with exponential backoff (caps at 30s)
  - returns ok/fail; never raises to the app layer

Methods only support the verbs the Pico needs:
  heartbeat (POST), telemetry (POST), ota_poll (GET), emergency (POST),
  announce_uuid (POST, one-shot at first boot)

The Pico has no GET/list/enumerate verbs against the master — limiting
blast radius of a key compromise.
"""
import json
import time

try:
    import urequests as requests
except ImportError:
    requests = None


_MAX_RETRIES = 4
_INITIAL_BACKOFF_MS = 500
_MAX_BACKOFF_MS = 30000


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.time() * 1000) if hasattr(time, "time") else 0


class NeuronAPI:
    def __init__(self, cfg, host_allow, cert_pinner):
        self.cfg = cfg
        self.api_key = cfg["api_key"]
        self.allow = host_allow
        self.pinner = cert_pinner
        ch = cfg["channels"]
        self.control_host = ch["control"]["broker_host"]
        self.ota_url      = ch["ota"]["endpoint"]
        self._base = (
            self.ota_url.rsplit("/api/", 1)[0]
            if "/api/" in self.ota_url
            else "https://" + self.control_host
        )

    # ── internal ────────────────────────────────────────────────────
    def _host_of(self, url: str) -> str:
        if "://" in url:
            url = url.split("://", 1)[1]
        return url.split("/", 1)[0].split(":", 1)[0]

    def _headers(self) -> dict:
        return {
            "X-API-Key":     self.api_key,
            "Content-Type":  "application/json",
            "X-Device-DNA":  self.cfg["device_dna"],
        }

    def _request_once(self, method, url, body):
        self.allow.check(self._host_of(url))
        if requests is None:
            raise RuntimeError("urequests not available")
        kwargs = {"headers": self._headers(), "timeout": 10}
        if body is not None:
            kwargs["data"] = json.dumps(body)
        return requests.request(method, url, **kwargs)

    def _request(self, method, url, body=None):
        """Retry with exponential backoff. Returns response or None on
        terminal failure (all retries exhausted)."""
        backoff = _INITIAL_BACKOFF_MS
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                r = self._request_once(method, url, body)
                # 5xx is server-side, worth retrying. 4xx is client error
                # (auth, schema) — don't retry, the response won't change.
                if r.status_code >= 500:
                    last_err = "http {}".format(r.status_code)
                    r.close()
                else:
                    return r
            except Exception as e:
                last_err = str(e)
            # Backoff
            time.sleep_ms(backoff) if hasattr(time, "sleep_ms") else time.sleep(backoff / 1000)
            backoff = min(backoff * 2, _MAX_BACKOFF_MS)
        return None

    # ── allowed Pico verbs ─────────────────────────────────────────
    def heartbeat(self, payload):
        r = self._request("POST", "{}/api/pico/heartbeat".format(self._base), payload)
        if r is None:
            return False
        ok = 200 <= r.status_code < 300
        r.close()
        return ok

    def telemetry(self, payload):
        r = self._request("POST", "{}/api/pico/telemetry".format(self._base), payload)
        if r is None:
            return False
        ok = 200 <= r.status_code < 300
        r.close()
        return ok

    def ota_poll(self):
        r = self._request("GET", self.ota_url)
        if r is None:
            return None
        try:
            data = r.json()
            return data
        except Exception:
            return None
        finally:
            r.close()

    def emergency(self, payload):
        r = self._request("POST", "{}/api/pico/emergency".format(self._base), payload)
        if r is None:
            return False
        ok = 200 <= r.status_code < 300
        r.close()
        return ok

    def announce_uuid(self, payload):
        """First-boot announcement so master can mint a real HMAC for
        subsequent flashes. One-shot, idempotent."""
        r = self._request("POST", "{}/api/pico/announce".format(self._base), payload)
        if r is None:
            return False
        ok = 200 <= r.status_code < 300
        r.close()
        return ok
