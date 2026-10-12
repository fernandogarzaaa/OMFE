"""HMAC webhook signing with timestamp-based replay protection.

Header format (Stripe-style):  X-OFM-Signature: t=<unix>,v1=<hex hmac_sha256(secret, "<t>.<body>")>

Receivers must (1) reject timestamps outside the tolerance window, (2) compare in constant
time, and (3) dedupe on the signature / delivery id for the window (done by the inbound
webhook table).
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-OFM-Signature"


class SignatureError(Exception):
    pass


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    msg = str(timestamp).encode() + b"." + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={compute_signature(secret, ts, body)}"


def parse_header(header: str) -> tuple[int, list[str]]:
    ts: int | None = None
    sigs: list[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                ts = int(value)
            except ValueError as exc:
                raise SignatureError("invalid timestamp") from exc
        elif key == "v1":
            sigs.append(value)
    if ts is None or not sigs:
        raise SignatureError("malformed signature header")
    return ts, sigs


def verify(secret: str, body: bytes, header: str | None, tolerance: int = 300, now: int | None = None) -> int:
    """Verify a signature header. Returns the signed timestamp; raises SignatureError."""
    if not header:
        raise SignatureError("missing signature")
    ts, sigs = parse_header(header)
    current = int(time.time()) if now is None else now
    if abs(current - ts) > tolerance:
        raise SignatureError("timestamp outside tolerance (possible replay)")
    expected = compute_signature(secret, ts, body)
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError("signature mismatch")
    return ts
