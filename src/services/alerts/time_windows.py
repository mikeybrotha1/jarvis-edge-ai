"""Active window / weekday checks for alert rules."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from storage.alert_records import AlertRuleRecord


def rule_is_active_at(rule: AlertRuleRecord, when: datetime) -> bool:
    """Return True when ``when`` falls in the rule's local active window."""

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone(ZoneInfo(rule.timezone))

    if rule.days_of_week:
        # Python Monday=0 … Sunday=6 matches our validated days.
        if local.weekday() not in rule.days_of_week:
            return False

    if rule.active_window_start and rule.active_window_end:
        start = _parse_hhmm(rule.active_window_start)
        end = _parse_hhmm(rule.active_window_end)
        current = local.timetz().replace(tzinfo=None)
        if start <= end:
            return start <= current <= end
        # Overnight window (e.g. 22:00–06:00).
        return current >= start or current <= end

    return True


def _parse_hhmm(value: str) -> time:
    hour_s, minute_s = value.split(":")
    return time(hour=int(hour_s), minute=int(minute_s))
