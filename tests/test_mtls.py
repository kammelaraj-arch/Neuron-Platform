"""mTLS issuance — CA bootstrap, edge cert mint, fingerprint format."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest


def test_ca_bootstraps_and_persists(isolated_master):
    from backend.security import mtls
    cert_pem_a = mtls.ca_cert_pem()
    fp_a = mtls.ca_fingerprint_sha256()
    assert "BEGIN CERTIFICATE" in cert_pem_a
    # Re-import the module to simulate a process restart; the persisted
    # CA on disk should be reused, not regenerated.
    import sys, importlib
    sys.modules.pop("backend.security.mtls", None)
    from backend.security import mtls as mtls_b
    cert_pem_b = mtls_b.ca_cert_pem()
    fp_b = mtls_b.ca_fingerprint_sha256()
    assert cert_pem_a == cert_pem_b
    assert fp_a == fp_b


def test_fingerprint_matches_access_policy_regex(isolated_master):
    """Fingerprints must satisfy the schema's
    ``^[A-Fa-f0-9:]{59,}$`` pattern."""
    import re
    from backend.security import mtls
    fp = mtls.ca_fingerprint_sha256()
    assert re.match(r"^[A-Fa-f0-9:]{59,}$", fp)
    # SHA-256 hex = 64 chars; with 31 colons inserted = 95 chars
    assert len(fp) == 95
    assert fp.count(":") == 31


def test_issue_edge_cert_signed_by_ca(isolated_master):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from backend.security import mtls

    issued = mtls.issue_edge_cert(subject_cn="edge-uk-wembley", validity_days=30)

    # Returned plaintext PEMs parse back into matching primitives.
    cert = x509.load_pem_x509_certificate(issued["cert_pem"].encode())
    key = serialization.load_pem_private_key(issued["key_pem"].encode(), password=None)
    assert cert.subject.rfc4514_string().endswith("edge-uk-wembley")

    # Signed by the CA we just bootstrapped.
    ca_cert = x509.load_pem_x509_certificate(mtls.ca_cert_pem().encode())
    assert cert.issuer == ca_cert.subject

    # Validity window roughly matches.
    now = datetime.now(timezone.utc)
    nva = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=timezone.utc)
    assert (nva - now) > timedelta(days=29)
    assert (nva - now) < timedelta(days=31)


def test_two_issued_certs_have_different_fingerprints(isolated_master):
    from backend.security import mtls
    a = mtls.issue_edge_cert(subject_cn="edge-a", validity_days=30)
    b = mtls.issue_edge_cert(subject_cn="edge-b", validity_days=30)
    assert a["fingerprint_sha256"] != b["fingerprint_sha256"]
    assert a["serial"] != b["serial"]
