"""mTLS issuance — Master mints a self-signed CA on first boot, then issues
per-Edge client certificates signed by that CA. Each cert's SHA-256
fingerprint is exposed so it can be pinned in the Edge access policy
(``access_policy.inbound.allow[].cert_fingerprints_sha256``).

Persistence:
  data/mtls/ca.crt              — long-lived self-signed CA cert (PEM)
  data/mtls/ca.key              — CA private key (PEM, chmod 0600)
  (per-edge certs are stored in the DB, not on disk)

Issued cert lifecycle:
  * Created via POST /api/mtls/edge-certs (admin) or /ui/secrets
  * Stored in EdgeCert table with sha256 fingerprint
  * Plaintext PEM (cert + key) returned exactly ONCE at issuance,
    just like API key plaintexts
  * Revoke moves status → revoked; the fingerprint stays for audit
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


_CA_DIR = Path("data/mtls")
_CA_CERT = _CA_DIR / "ca.crt"
_CA_KEY = _CA_DIR / "ca.key"
_CA_NAME = "Neuron Platform mTLS CA"


def _ensure_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    _CA_DIR.mkdir(parents=True, exist_ok=True)
    if _CA_CERT.exists() and _CA_KEY.exists():
        cert_pem = _CA_CERT.read_bytes()
        key_pem = _CA_KEY.read_bytes()
        return x509.load_pem_x509_certificate(cert_pem), \
               serialization.load_pem_private_key(key_pem, password=None)  # type: ignore[return-value]

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _CA_NAME)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    _CA_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _CA_KEY.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        _CA_KEY.chmod(0o600)
        _CA_CERT.chmod(0o644)
    except OSError:
        pass
    return cert, key


def ca_cert_pem() -> str:
    cert, _ = _ensure_ca()
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def ca_fingerprint_sha256() -> str:
    cert, _ = _ensure_ca()
    digest = cert.fingerprint(hashes.SHA256()).hex()
    # Format as colon-separated for the access_policy schema's regex
    # (^[A-Fa-f0-9:]{59,}$).
    return ":".join(digest[i:i+2] for i in range(0, len(digest), 2)).upper()


def _fp_sha256(cert: x509.Certificate) -> str:
    digest = cert.fingerprint(hashes.SHA256()).hex()
    return ":".join(digest[i:i+2] for i in range(0, len(digest), 2)).upper()


def issue_edge_cert(
    *,
    subject_cn: str,
    validity_days: int = 365,
    sans_dns: list[str] | None = None,
) -> dict[str, Any]:
    """Mint a fresh client cert + key signed by the platform CA.

    Returns: {cert_pem, key_pem, fingerprint_sha256, issued_at, expires_at,
              serial, subject_cn}
    The plaintext PEM is shown ONCE; the caller is responsible for
    persisting only the fingerprint + metadata.
    """
    ca_cert, ca_key = _ensure_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=True, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
                       critical=False)
    )
    if sans_dns:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(d) for d in sans_dns]),
            critical=False,
        )
    cert = builder.sign(ca_key, hashes.SHA256())

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "subject_cn": subject_cn,
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "fingerprint_sha256": _fp_sha256(cert),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=validity_days)).isoformat(),
        "serial": str(cert.serial_number),
    }
