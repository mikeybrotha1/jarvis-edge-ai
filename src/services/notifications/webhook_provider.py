"""HTTP webhook notification provider (v0.9.0)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from services.notifications.provider import DeliveryResult
from services.notifications.signing import build_signature_headers
from services.notifications.ssrf import SSRFValidationError, validate_webhook_url
from storage.notification_orm import NotificationChannelType
from storage.notification_records import NotificationTargetRecord

USER_AGENT = "JarvisEdgeAI-NotificationWorker/0.9.0"
RETRYABLE_STATUS = frozenset({408, 425, 429})


def _safe_url_for_log(url: str) -> str:
    """Hostname + path only; strip credentials and query."""

    try:
        p = urlparse(url)
        host = p.hostname or "unknown"
        return urlunparse((p.scheme, host, p.path or "/", "", "", ""))
    except Exception:  # noqa: BLE001
        return "<invalid-url>"


def _sanitize_error(message: str, *, max_len: int = 512) -> str:
    text = " ".join(str(message).split())
    # Strip common secret-looking fragments
    if "secret" in text.lower() or "authorization" in text.lower():
        text = "request failed (details redacted)"
    return text[:max_len]


def classify_http_status(status: int) -> tuple[bool, bool]:
    """Return (success, retryable) for an HTTP status code."""

    if 200 <= status < 300:
        return True, False
    if status in RETRYABLE_STATUS or status >= 500:
        return False, True
    # Most other 4xx are terminal
    return False, False


class WebhookNotificationProvider:
    channel_type = NotificationChannelType.WEBHOOK.value

    def __init__(
        self,
        *,
        request_timeout_seconds: float = 5.0,
        max_request_bytes: int = 65536,
        max_response_bytes: int = 8192,
        allow_private_targets: bool = False,
        logger: logging.Logger | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.request_timeout_seconds = request_timeout_seconds
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.allow_private_targets = allow_private_targets
        self._logger = logger or logging.getLogger(__name__)
        self._client = client
        self._owns_client = client is None

    def supports(self, target: Any) -> bool:
        channel = getattr(target, "channel_type", None)
        if channel is None:
            return False
        value = channel.value if hasattr(channel, "value") else str(channel)
        return value == self.channel_type

    def deliver(
        self,
        target: Any,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        signing_secret: str | None = None,
    ) -> DeliveryResult:
        url = getattr(target, "url", None)
        if not url:
            return DeliveryResult(
                success=False,
                retryable=False,
                error_code="missing_url",
                error_message_sanitized="Target URL is missing.",
            )

        try:
            validate_webhook_url(
                url,
                allow_private_targets=self.allow_private_targets,
                resolve_dns=True,
            )
        except SSRFValidationError as error:
            return DeliveryResult(
                success=False,
                retryable=False,
                error_code="ssrf_blocked",
                error_message_sanitized=_sanitize_error(str(error)),
            )

        try:
            body = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            return DeliveryResult(
                success=False,
                retryable=False,
                error_code="payload_encode_error",
                error_message_sanitized=_sanitize_error(str(error)),
            )

        if len(body) > self.max_request_bytes:
            return DeliveryResult(
                success=False,
                retryable=False,
                error_code="payload_too_large",
                error_message_sanitized=(
                    f"Request body exceeds max_request_bytes "
                    f"({self.max_request_bytes})."
                ),
            )

        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Jarvis-Delivery-ID": idempotency_key,
            "Accept": "application/json, text/plain, */*",
        }
        if signing_secret:
            headers.update(
                build_signature_headers(body, signing_secret)
            )

        timeout = httpx.Timeout(self.request_timeout_seconds)
        started = time.perf_counter()
        client = self._client
        close_after = False
        if client is None:
            client = httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )
            close_after = True

        try:
            response = client.post(url, content=body, headers=headers)
            duration_ms = (time.perf_counter() - started) * 1000.0
            raw = response.content[: self.max_response_bytes]
            truncated = raw.decode("utf-8", errors="replace")
            if len(response.content) > self.max_response_bytes:
                truncated = truncated[: self.max_response_bytes]
            success, retryable = classify_http_status(response.status_code)
            summary = f"HTTP {response.status_code}"
            if success:
                return DeliveryResult(
                    success=True,
                    retryable=False,
                    response_status=response.status_code,
                    response_summary=summary,
                    duration_ms=duration_ms,
                    response_body_truncated=truncated[:512] or None,
                )
            return DeliveryResult(
                success=False,
                retryable=retryable,
                response_status=response.status_code,
                response_summary=summary,
                error_code=f"http_{response.status_code}",
                error_message_sanitized=_sanitize_error(
                    f"Webhook returned HTTP {response.status_code}"
                ),
                duration_ms=duration_ms,
                response_body_truncated=truncated[:512] or None,
            )
        except httpx.TimeoutException as error:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._logger.warning(
                "Webhook timeout to %s", _safe_url_for_log(url)
            )
            return DeliveryResult(
                success=False,
                retryable=True,
                error_code="timeout",
                error_message_sanitized=_sanitize_error(
                    f"Request timeout: {error.__class__.__name__}"
                ),
                duration_ms=duration_ms,
            )
        except httpx.HTTPError as error:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._logger.warning(
                "Webhook connection error to %s: %s",
                _safe_url_for_log(url),
                error.__class__.__name__,
            )
            return DeliveryResult(
                success=False,
                retryable=True,
                error_code="connection_error",
                error_message_sanitized=_sanitize_error(
                    f"Connection error: {error.__class__.__name__}"
                ),
                duration_ms=duration_ms,
            )
        finally:
            if close_after and client is not None:
                client.close()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
