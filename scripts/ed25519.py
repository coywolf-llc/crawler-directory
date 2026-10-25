"""Ed25519 (RFC 8032) in pure Python, standard library only, so validate.py and the GitHub workflow can check
dist/v1/manifest.json.sig without installing anything.

This follows the reference code in RFC 8032, section 6. It isn't constant-time: use it to verify public data
(and, in tests, to sign with throwaway keys), never to sign with a real private key. Bots Manager signs releases
with the `cryptography` package.
"""

from __future__ import annotations

import hashlib

p = 2**255 - 19
q = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, p - 2, p)


d = -121665 * _inv(121666) % p
_SQRT_M1 = pow(2, (p - 1) // 4, p)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= p:
        return None
    x2 = (y * y - 1) * _inv(d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * _SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_GY = 4 * _inv(5) % p
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % p)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p
    D = 2 * P[2] * Q[2] % p
    E, F, Gg, H = B - A, D - C, D + C, B + A
    return (E * F % p, Gg * H % p, F * Gg % p, E * H % p)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _compress(P) -> bytes:
    zinv = _inv(P[2])
    x = P[0] * zinv % p
    y = P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _expand(secret: bytes) -> tuple[int, bytes]:
    if len(secret) != 32:
        raise ValueError("an Ed25519 secret key is 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, G))


def sign(secret: bytes, msg: bytes) -> bytes:
    """Reference signing, for tests only (not constant-time)."""
    a, prefix = _expand(secret)
    A = _compress(_mul(a, G))
    r = _sha512_int(prefix + msg) % q
    Rs = _compress(_mul(r, G))
    h = _sha512_int(Rs + A + msg) % q
    s = (r + h * a) % q
    return Rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    if A is None:
        return False
    Rs = signature[:32]
    R = _decompress(Rs)
    if R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= q:
        return False
    h = _sha512_int(Rs + public + msg) % q
    return _equal(_mul(s, G), _add(R, _mul(h, A)))
