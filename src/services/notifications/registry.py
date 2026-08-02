"""Provider registry for notification channels."""

from __future__ import annotations

from typing import Any

from services.notifications.provider import NotificationProvider


class NotificationProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, NotificationProvider] = {}

    def register(self, provider: NotificationProvider) -> None:
        self._providers[provider.channel_type] = provider

    def get_for_target(self, target: Any) -> NotificationProvider | None:
        channel = getattr(target, "channel_type", None)
        if channel is None:
            return None
        value = channel.value if hasattr(channel, "value") else str(channel)
        provider = self._providers.get(value)
        if provider is None or not provider.supports(target):
            return None
        return provider

    def get(self, channel_type: str) -> NotificationProvider | None:
        return self._providers.get(channel_type)
