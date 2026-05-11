"""Tiny pure-Python Ed25519 verifier (RFC 8032). Verify-only — the device
never signs, so we don't need the sign path. ~5 KiB compiled, runs on
MicroPython on the Pico 2 W.
"""
import hashlib

_q = 2 ** 255 - 19
_l = 2 ** 252 + 27742317777372353535851937790883648493


def _modp_inv(x):
    return pow(x, _q - 2, _q)


_d = -121665 * _modp_inv(121666) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _sha512(data):
    return hashlib.sha512(data).digest()


def _sha512_int(data):
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


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _q
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _q
    C = 2 * P[3] * Q[3] * _d % _q
    D = 2 * P[2] * Q[2] % _q
    return ((B - A) * (D - C) % _q,
            (D + C) * (B + A) % _q,
            (D - C) * (D + C) % _q,
            (B - A) * (B + A) % _q)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _compress(P):
    zinv = _modp_inv(P[2])
    x = P[0] * zinv % _q
    y = P[1] * zinv % _q
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        raise ValueError
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    x = _x_recover(y)
    if x & 1 != sign:
        x = _q - x
    return (x, y, 1, x * y % _q)


def verify(pub, msg, sig):
    if len(pub) != 32 or len(sig) != 64:
        return False
    try:
        A = _decompress(pub)
    except Exception:
        return False
    R_bytes = sig[:32]
    s = int.from_bytes(sig[32:], "little")
    if s >= _l:
        return False
    try:
        R = _decompress(R_bytes)
    except Exception:
        return False
    k = _sha512_int(R_bytes + pub + msg) % _l
    sB = _mul(s, _B)
    kA = _mul(k, A)
    return _compress(sB) == _compress(_add(R, kA))
