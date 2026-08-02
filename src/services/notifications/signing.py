"""HMAC-SHA256 request signing for webhook deliveries."""

from __future__ import annotations

import hashlib
import hmac
import time


def build_signature_headers(
    body: bytes,
    signing_secret: str,
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Return X-Jarvis-Timestamp and X-Jarvis-Signature headers.

    Signature format: ``sha256=<hex>`` over
    ``{timestamp}.{body}`` (UTF-8 timestamp + '.' + exact body bytes).
    """

    ts = int(timestamp if timestamp is not None else time.time())
    message = f"{ts}.".encode("utf-8") + body
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Jarvis-Timestamp": str(ts),
        "X-Jarvis-Signature": f"sha256={digest}",
    }


def verify_signature(
    body: bytes,
    signing_secret: str,
    *,
    timestamp: str | int,
    signature_header: str,
) -> bool:
    """Verify a signature header (for tests / receiver documentation)."""

    expected = build_signature_headers(
        body, signing_secret, timestamp=int(timestamp)
    )
    got = signature_header.strip()
    want = expected["X-Jarvis-Signature"]
    return hmac.compare_digest(got, want)
