"""Host allow-list + TLS cert pinning.

Refuses outbound connections to anything not in the allow-list,
and any TLS server cert whose SHA-256 doesn't match the pinned value.
Both checks fire BEFORE any application bytes leave the device, so a
compromised app layer can't exfiltrate to a third party or fall for
a MITM with a leaked CA.

Apps MUST go through neuron.api which calls these. There's no other
network entry point in the baseline.
"""

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib


class HostNotAllowed(Exception):
    pass


class CertPinMismatch(Exception):
    pass


class HostAllowList:
    """Exact-match allow-list. No wildcards — deliberate."""

    def __init__(self, hosts):
        self._allowed = set()
        for h in hosts:
            h = (h or "").strip().lower()
            # Strip scheme, port, path
            if "://" in h:
                h = h.split("://", 1)[1]
            h = h.split(":", 1)[0].split("/", 1)[0]
            if h:
                self._allowed.add(h)

    def check(self, host: str) -> None:
        h = (host or "").strip().lower()
        if "://" in h:
            h = h.split("://", 1)[1]
        h = h.split(":", 1)[0].split("/", 1)[0]
        if h not in self._allowed:
            raise HostNotAllowed(
                "host {!r} not in allow-list {}".format(h, sorted(self._allowed))
            )

    def allows(self, host: str) -> bool:
        try:
            self.check(host)
            return True
        except HostNotAllowed:
            return False


class CertPinner:
    """SHA-256 fingerprint pinning. Accepts hex string with or without
    colons (so the master can write either "AB:CD:..." or "abcd...")."""

    def __init__(self, fp_sha256: str):
        self.expected = (fp_sha256 or "").replace(":", "").lower()
        if self.expected and len(self.expected) != 64:
            raise ValueError("cert SHA-256 must be 64 hex chars (got {})".format(len(self.expected)))

    def check_der(self, der_bytes: bytes) -> None:
        if not self.expected:
            # Empty pin = not configured. Refuse rather than fall-open.
            raise CertPinMismatch("no cert pin configured — refusing TLS")
        got = hashlib.sha256(der_bytes).digest()
        got_hex = "".join("{:02x}".format(b) for b in got)
        if got_hex != self.expected:
            raise CertPinMismatch(
                "server cert SHA-256={} != pinned={}".format(got_hex[:16] + "…", self.expected[:16] + "…")
            )
