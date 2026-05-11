"""Ed25519 signing key for OTA manifests.

The private key is auto-generated on first boot, persisted to
``data/ota_signing.key`` (chmod 0600), and never exposed via any API.
The matching public key is exposed at ``GET /api/ota/pubkey`` so that
Pico firmware can be flashed with it embedded for offline verification.

Implementation notes:
  * We use cryptography.hazmat.primitives.asymmetric.ed25519 — already a
    transitive dependency via httpx[http2] is not pulled in by default,
    but we don't need that. Instead, fall back to a pure-Python Ed25519
    implementation (a tiny ed25519 RFC8032 reference implementation
    bundled in this module) so requirements.txt stays lean.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

# RFC 8032 reference Ed25519 — tiny, dependency-free, suitable for the
# moderate signing volume of OTA manifests (a few per day in worst case).

_b = 256
_q = 2 ** 255 - 19
_l = 2 ** 252 + 27742317777372353535851937790883648493


def _modp_inv(x):
    return pow(x, _q - 2, _q)


_d = -121665 * _modp_inv(121666) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little")


def _x_recover(y):
    xx = (y * y - 1) * _modp_inv(_d * y * y + 1) % _q
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = 4 * _modp_inv(5) % _q
_Bx = _x_recover(_By)
_B = (_Bx % _q, _By % _q, 1, (_Bx * _By) % _q)


def _point_add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _q
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _q
    C = 2 * P[3] * Q[3] * _d % _q
    D = 2 * P[2] * Q[2] % _q
    E = B - A
    F = D - C
    G = D + C
    H = B + A
    return (E * F % _q, G * H % _q, F * G % _q, E * H % _q)


def _point_mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _point_compress(P):
    zinv = _modp_inv(P[2])
    x = P[0] * zinv % _q
    y = P[1] * zinv % _q
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(s):
    if len(s) != 32:
        raise ValueError("invalid point length")
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    x = _x_recover(y)
    if x & 1 != sign:
        x = _q - x
    return (x, y, 1, x * y % _q)


def _secret_expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def public_from_seed(seed: bytes) -> bytes:
    a, _ = _secret_expand(seed)
    return _point_compress(_point_mul(a, _B))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(seed)
    A = _point_compress(_point_mul(a, _B))
    r = _sha512_int(prefix + msg) % _l
    R = _point_compress(_point_mul(r, _B))
    k = _sha512_int(R + A + msg) % _l
    s = (r + k * a) % _l
    return R + int.to_bytes(s, 32, "little")


def verify(pub: bytes, msg: bytes, signature: bytes) -> bool:
    if len(pub) != 32 or len(signature) != 64:
        return False
    try:
        A = _point_decompress(pub)
    except Exception:
        return False
    R_bytes = signature[:32]
    s = int.from_bytes(signature[32:], "little")
    if s >= _l:
        return False
    try:
        R = _point_decompress(R_bytes)
    except Exception:
        return False
    k = _sha512_int(R_bytes + pub + msg) % _l
    sB = _point_mul(s, _B)
    kA = _point_mul(k, A)
    R_plus_kA = _point_add(R, kA)
    return _point_compress(sB) == _point_compress(R_plus_kA)


# ── Persistence & API ────────────────────────────────────────────────────────

_SEED_PATH = Path("data/ota_signing.key")
_PUB_PATH = Path("data/ota_signing.pub")
_KID = "ota-default-1"


def _ensure_seed() -> bytes:
    _SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _SEED_PATH.exists():
        return _SEED_PATH.read_bytes()[:32]
    seed = os.urandom(32)
    _SEED_PATH.write_bytes(seed)
    pub = public_from_seed(seed)
    _PUB_PATH.write_bytes(pub)
    try:
        _SEED_PATH.chmod(0o600)
        _PUB_PATH.chmod(0o644)
    except OSError:
        pass
    return seed


def signing_kid() -> str:
    return _KID


def public_key_b64() -> str:
    seed = _ensure_seed()
    pub = public_from_seed(seed)
    return base64.b64encode(pub).decode("ascii")


def public_key_hex() -> str:
    seed = _ensure_seed()
    return public_from_seed(seed).hex()


def sign_b64(msg: bytes) -> dict:
    seed = _ensure_seed()
    sig = sign(seed, msg)
    return {
        "alg": "ed25519",
        "kid": _KID,
        "value": base64.b64encode(sig).decode("ascii"),
    }


def verify_b64(msg: bytes, sig_b64: str, pub_b64: str | None = None) -> bool:
    pub = (
        base64.b64decode(pub_b64)
        if pub_b64
        else public_from_seed(_ensure_seed())
    )
    return verify(pub, msg, base64.b64decode(sig_b64))
