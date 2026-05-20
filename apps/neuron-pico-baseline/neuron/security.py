"""Host allow-list + TLS cert pinning enforcement.

Hard rule from CLAUDE.md: a Pico MUST NOT initiate outbound traffic to
any host not in its allow-list, and MUST NOT accept a TLS handshake
whose server cert SHA-256 fingerprint differs from the pinned value
in config.json. These checks happen before any HTTP/MQTT bytes leave
the device — making firmware-compromise exfiltration much harder.

Apps that need to talk to a peer MUST go through `neuron.api` which
calls these checks. There is no escape hatch.
"""

try:
    import ussl as ssl
except ImportError:
    try:
        import ssl
    except ImportError:
        ssl = None

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib


class HostNotAllowed(Exception):
    pass


class CertPinMismatch(Exception):
    pass


class HostAllowList:
    """Refuse connections to anything not in the explicit allow-list."""

    def __init__(self, hosts):
        # Lower-case, strip ports/paths. Exact-match — no wildcards by design.
        self._allowed = {h.lower().split(":")[0].split("/")[0] for h in hosts}

    def check(self, host: str) -> None:
        h = (host or "").lower().split(":")[0].split("/")[0]
        if h not in self._allowed:
            raise HostNotAllowed(
                f"host {h!r} not in allow-list {sorted(self._allowed)}"
            )

    def allows(self, host: str) -> bool:
        try:
            self.check(host)
            return True
        except HostNotAllowed:
            return False


class CertPinner:
    """SHA-256 fingerprint pinning. fp is "AB:CD:…" hex, colon-separated."""

    def __init__(self, fp_sha256: str):
        self.expected = fp_sha256.replace(":", "").lower()

    def check_der(self, der_bytes: bytes) -> None:
        got = hashlib.sha256(der_bytes).digest()
        got_hex = "".join("{:02x}".format(b) for b in got)
        if got_hex != self.expected:
            raise CertPinMismatch(
                f"server cert SHA-256={got_hex} != pinned={self.expected}"
            )
