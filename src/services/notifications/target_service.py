"""Notification target CRUD and validation service."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from services.notifications.secrets import (
    SecretEncryptionError,
    encrypt_secret,
    encryption_key_available,
)
from services.notifications.ssrf import SSRFValidationError, validate_webhook_url
from storage.entity_records import PageResult
from storage.notification_orm import NotificationChannelType
from storage.notification_records import (
    NotificationTargetCreate,
    NotificationTargetRecord,
    NotificationTargetUpdate,
)
from storage.notification_repositories import (
    NotificationConflictError,
    NotificationTargetRepository,
    RuleNotificationTargetRepository,
)

_VALID_SEVERITIES = frozenset({"info", "warning", "critical"})


class TargetValidationError(ValueError):
    pass


class TargetNotFoundError(LookupError):
    pass


class NotificationTargetService:
    def __init__(
        self,
        target_repository: NotificationTargetRepository,
        association_repository: RuleNotificationTargetRepository | None = None,
        *,
        allow_private_targets: bool = False,
        max_metadata_bytes: int = 8192,
        logger: logging.Logger | None = None,
    ) -> None:
        self._targets = target_repository
        self._associations = association_repository
        self.allow_private_targets = allow_private_targets
        self.max_metadata_bytes = max_metadata_bytes
        self._logger = logger or logging.getLogger(__name__)

    def create(self, body: dict[str, Any]) -> NotificationTargetRecord:
        data = self._parse_create(body)
        encrypted = None
        if data.signing_secret:
            try:
                encrypted = encrypt_secret(data.signing_secret)
            except SecretEncryptionError as error:
                raise TargetValidationError(str(error)) from error
        return self._targets.create(
            data, signing_secret_encrypted=encrypted
        )

    def get(self, target_id: UUID) -> NotificationTargetRecord:
        row = self._targets.get_by_id(target_id)
        if row is None:
            raise TargetNotFoundError(f"Notification target not found: {target_id}")
        return row

    def update(
        self, target_id: UUID, body: dict[str, Any]
    ) -> NotificationTargetRecord:
        if self._targets.get_by_id(target_id) is None:
            raise TargetNotFoundError(f"Notification target not found: {target_id}")
        data, set_secret, encrypted = self._parse_update(body)
        try:
            return self._targets.update(
                target_id,
                data,
                signing_secret_encrypted=encrypted,
                set_signing_secret=set_secret,
            )
        except LookupError as error:
            raise TargetNotFoundError(str(error)) from error

    def list_targets(
        self,
        *,
        enabled: bool | None = None,
        is_global: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PageResult:
        return self._targets.list_targets(
            enabled=enabled,
            is_global=is_global,
            limit=limit,
            offset=offset,
        )

    def associate(self, rule_id: UUID, target_id: UUID) -> None:
        if self._associations is None:
            raise RuntimeError("Association repository not configured")
        if self._targets.get_by_id(target_id) is None:
            raise TargetNotFoundError(f"Notification target not found: {target_id}")
        self._associations.associate(rule_id, target_id, enabled=True)

    def disassociate(self, rule_id: UUID, target_id: UUID) -> None:
        if self._associations is None:
            raise RuntimeError("Association repository not configured")
        ok = self._associations.disassociate(rule_id, target_id)
        if not ok:
            raise TargetNotFoundError(
                f"Association not found for rule {rule_id} target {target_id}"
            )

    def list_for_rule(self, rule_id: UUID) -> list[NotificationTargetRecord]:
        if self._associations is None:
            raise RuntimeError("Association repository not configured")
        return self._associations.list_for_rule(rule_id)

    def _parse_create(self, body: dict[str, Any]) -> NotificationTargetCreate:
        if not isinstance(body, dict):
            raise TargetValidationError("Body must be a JSON object.")
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise TargetValidationError("name is required.")
        if len(name.strip()) > 128:
            raise TargetValidationError("name must be <= 128 characters.")
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            raise TargetValidationError("url is required.")
        try:
            validate_webhook_url(
                url.strip(),
                allow_private_targets=self.allow_private_targets,
                resolve_dns=True,
            )
        except SSRFValidationError as error:
            raise TargetValidationError(str(error)) from error

        channel = body.get("channel_type", "webhook")
        if channel != "webhook":
            raise TargetValidationError(
                "Only channel_type=webhook is supported in v0.9.0."
            )
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TargetValidationError("enabled must be a boolean.")
        is_global = body.get("is_global", False)
        if not isinstance(is_global, bool):
            raise TargetValidationError("is_global must be a boolean.")
        severity_filters = body.get("severity_filters") or []
        if not isinstance(severity_filters, list):
            raise TargetValidationError("severity_filters must be a list.")
        cleaned_sev: list[str] = []
        for item in severity_filters:
            if not isinstance(item, str) or item.lower() not in _VALID_SEVERITIES:
                raise TargetValidationError(
                    f"Invalid severity filter: {item!r}"
                )
            cleaned_sev.append(item.lower())
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise TargetValidationError("metadata must be an object.")
        encoded = json.dumps(metadata, default=str)
        if len(encoded.encode("utf-8")) > self.max_metadata_bytes:
            raise TargetValidationError("metadata exceeds size limit.")
        signing_secret = body.get("signing_secret")
        if signing_secret is not None:
            if not isinstance(signing_secret, str) or not signing_secret:
                raise TargetValidationError(
                    "signing_secret must be a non-empty string when provided."
                )
            if len(signing_secret) > 512:
                raise TargetValidationError("signing_secret too long.")
            if not encryption_key_available():
                raise TargetValidationError(
                    "signing_secret requires JARVIS_NOTIFICATIONS_ENCRYPTION_KEY."
                )
        return NotificationTargetCreate(
            name=name.strip(),
            url=url.strip(),
            enabled=enabled,
            is_global=is_global,
            signing_secret=signing_secret,
            severity_filters=cleaned_sev,
            metadata=dict(metadata),
            channel_type=NotificationChannelType.WEBHOOK,
        )

    def _parse_update(
        self, body: dict[str, Any]
    ) -> tuple[NotificationTargetUpdate, bool, str | None]:
        if not isinstance(body, dict):
            raise TargetValidationError("Body must be a JSON object.")
        name = body.get("name") if "name" in body else None
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise TargetValidationError("name must be a non-empty string.")
        url = body.get("url") if "url" in body else None
        if url is not None:
            if not isinstance(url, str) or not url.strip():
                raise TargetValidationError("url must be a non-empty string.")
            try:
                validate_webhook_url(
                    url.strip(),
                    allow_private_targets=self.allow_private_targets,
                    resolve_dns=True,
                )
            except SSRFValidationError as error:
                raise TargetValidationError(str(error)) from error
        enabled = body.get("enabled") if "enabled" in body else None
        if enabled is not None and not isinstance(enabled, bool):
            raise TargetValidationError("enabled must be a boolean.")
        is_global = body.get("is_global") if "is_global" in body else None
        if is_global is not None and not isinstance(is_global, bool):
            raise TargetValidationError("is_global must be a boolean.")
        severity_filters = None
        if "severity_filters" in body:
            raw = body["severity_filters"] or []
            if not isinstance(raw, list):
                raise TargetValidationError("severity_filters must be a list.")
            severity_filters = []
            for item in raw:
                if (
                    not isinstance(item, str)
                    or item.lower() not in _VALID_SEVERITIES
                ):
                    raise TargetValidationError(
                        f"Invalid severity filter: {item!r}"
                    )
                severity_filters.append(item.lower())
        metadata = body.get("metadata") if "metadata" in body else None
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise TargetValidationError("metadata must be an object.")
            encoded = json.dumps(metadata, default=str)
            if len(encoded.encode("utf-8")) > self.max_metadata_bytes:
                raise TargetValidationError("metadata exceeds size limit.")
        clear_secret = bool(body.get("clear_signing_secret", False))
        signing_secret = body.get("signing_secret") if "signing_secret" in body else None
        set_secret = False
        encrypted = None
        if clear_secret:
            set_secret = False
        elif signing_secret is not None:
            if not isinstance(signing_secret, str) or not signing_secret:
                raise TargetValidationError(
                    "signing_secret must be a non-empty string when provided."
                )
            if not encryption_key_available():
                raise TargetValidationError(
                    "signing_secret requires JARVIS_NOTIFICATIONS_ENCRYPTION_KEY."
                )
            try:
                encrypted = encrypt_secret(signing_secret)
            except SecretEncryptionError as error:
                raise TargetValidationError(str(error)) from error
            set_secret = True
        return (
            NotificationTargetUpdate(
                name=name.strip() if isinstance(name, str) else None,
                url=url.strip() if isinstance(url, str) else None,
                enabled=enabled,
                is_global=is_global,
                clear_signing_secret=clear_secret,
                severity_filters=severity_filters,
                metadata=dict(metadata) if metadata is not None else None,
            ),
            set_secret,
            encrypted,
        )


# re-export conflict for routes
__all__ = [
    "NotificationConflictError",
    "NotificationTargetService",
    "TargetNotFoundError",
    "TargetValidationError",
]
