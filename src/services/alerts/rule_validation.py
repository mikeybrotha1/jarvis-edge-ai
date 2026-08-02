"""Alert rule validation helpers (v0.8.0)."""

from __future__ import annotations

import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from storage.alert_orm import AlertRuleType, AlertSeverity
from storage.alert_records import AlertRuleCreate, AlertRuleUpdate
from storage.timeline_models import ALL_TIMELINE_EVENT_TYPES

# Source events rules may observe (not alert_*).
SUPPORTED_SOURCE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "entity_created",
        "entity_closed",
        "observation_recorded",
        "zone_entered",
        "zone_exited",
        "zone_occupancy_changed",
    }
)

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_NAME = 128
MAX_COOLDOWN = 86400
MAX_METADATA_BYTES = 8192
MAX_FILTER_ITEMS = 32


class RuleValidationError(ValueError):
    pass


def validate_rule_create(
    data: dict[str, Any],
    *,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
    default_cooldown: int = 60,
) -> AlertRuleCreate:
    name = _require_name(data.get("name"))
    rule_type = _parse_rule_type(data.get("rule_type"))
    severity = _parse_severity(data.get("severity", "warning"))
    enabled = bool(data.get("enabled", True))
    source_event_types = _parse_string_list(
        data.get("source_event_types"), "source_event_types"
    )
    camera_ids = _parse_string_list(data.get("camera_ids"), "camera_ids")
    zone_ids = _parse_string_list(data.get("zone_ids"), "zone_ids")
    entity_types = _parse_string_list(data.get("entity_types"), "entity_types")
    occupancy_threshold = data.get("occupancy_threshold")
    occupancy_duration_seconds = data.get("occupancy_duration_seconds")
    dwell_threshold_seconds = data.get("dwell_threshold_seconds")
    if occupancy_threshold is not None:
        occupancy_threshold = _positive_int(
            occupancy_threshold, "occupancy_threshold"
        )
    if occupancy_duration_seconds is not None:
        occupancy_duration_seconds = _nonneg_int(
            occupancy_duration_seconds, "occupancy_duration_seconds"
        )
        if occupancy_duration_seconds > MAX_COOLDOWN:
            raise RuleValidationError(
                f"occupancy_duration_seconds cannot exceed {MAX_COOLDOWN}."
            )
    if dwell_threshold_seconds is not None:
        dwell_threshold_seconds = _positive_int(
            dwell_threshold_seconds, "dwell_threshold_seconds"
        )
    window_start = _parse_time_opt(data.get("active_window_start"))
    window_end = _parse_time_opt(data.get("active_window_end"))
    if (window_start is None) != (window_end is None):
        raise RuleValidationError(
            "active_window_start and active_window_end must both be set or both omitted."
        )
    timezone = _parse_timezone(data.get("timezone") or "UTC")
    days = _parse_days(data.get("days_of_week"))
    cooldown = data.get("cooldown_seconds", default_cooldown)
    cooldown = _nonneg_int(cooldown, "cooldown_seconds")
    if cooldown > MAX_COOLDOWN:
        raise RuleValidationError(
            f"cooldown_seconds cannot exceed {MAX_COOLDOWN}."
        )
    metadata = _parse_metadata(
        data.get("metadata") or {}, max_bytes=max_metadata_bytes
    )

    _validate_type_fields(
        rule_type,
        source_event_types=source_event_types,
        occupancy_threshold=occupancy_threshold,
        occupancy_duration_seconds=occupancy_duration_seconds,
        dwell_threshold_seconds=dwell_threshold_seconds,
        zone_ids=zone_ids,
    )

    return AlertRuleCreate(
        name=name,
        rule_type=rule_type,
        enabled=enabled,
        source_event_types=source_event_types,
        camera_ids=camera_ids,
        zone_ids=zone_ids,
        entity_types=entity_types,
        occupancy_threshold=occupancy_threshold,
        occupancy_duration_seconds=occupancy_duration_seconds,
        dwell_threshold_seconds=dwell_threshold_seconds,
        active_window_start=window_start,
        active_window_end=window_end,
        timezone=timezone,
        days_of_week=days,
        cooldown_seconds=cooldown,
        severity=severity,
        metadata=metadata,
    )


def validate_rule_update(
    data: dict[str, Any],
    *,
    current_type: AlertRuleType,
    max_metadata_bytes: int = MAX_METADATA_BYTES,
) -> AlertRuleUpdate:
    kwargs: dict[str, Any] = {}
    if "name" in data and data["name"] is not None:
        kwargs["name"] = _require_name(data["name"])
    if "enabled" in data and data["enabled"] is not None:
        kwargs["enabled"] = bool(data["enabled"])
    if "source_event_types" in data and data["source_event_types"] is not None:
        kwargs["source_event_types"] = _parse_string_list(
            data["source_event_types"], "source_event_types"
        )
    if "camera_ids" in data and data["camera_ids"] is not None:
        kwargs["camera_ids"] = _parse_string_list(
            data["camera_ids"], "camera_ids"
        )
    if "zone_ids" in data and data["zone_ids"] is not None:
        kwargs["zone_ids"] = _parse_string_list(data["zone_ids"], "zone_ids")
    if "entity_types" in data and data["entity_types"] is not None:
        kwargs["entity_types"] = _parse_string_list(
            data["entity_types"], "entity_types"
        )
    if "occupancy_threshold" in data:
        if data["occupancy_threshold"] is None:
            kwargs["clear_occupancy_threshold"] = True
        else:
            kwargs["occupancy_threshold"] = _positive_int(
                data["occupancy_threshold"], "occupancy_threshold"
            )
    if "occupancy_duration_seconds" in data:
        if data["occupancy_duration_seconds"] is None:
            kwargs["clear_occupancy_duration_seconds"] = True
        else:
            duration = _nonneg_int(
                data["occupancy_duration_seconds"],
                "occupancy_duration_seconds",
            )
            if duration > MAX_COOLDOWN:
                raise RuleValidationError(
                    f"occupancy_duration_seconds cannot exceed {MAX_COOLDOWN}."
                )
            kwargs["occupancy_duration_seconds"] = duration
    if "dwell_threshold_seconds" in data:
        if data["dwell_threshold_seconds"] is None:
            kwargs["clear_dwell_threshold_seconds"] = True
        else:
            kwargs["dwell_threshold_seconds"] = _positive_int(
                data["dwell_threshold_seconds"], "dwell_threshold_seconds"
            )
    if "active_window_start" in data:
        if data["active_window_start"] is None:
            kwargs["clear_active_window_start"] = True
        else:
            kwargs["active_window_start"] = _parse_time_opt(
                data["active_window_start"]
            )
    if "active_window_end" in data:
        if data["active_window_end"] is None:
            kwargs["clear_active_window_end"] = True
        else:
            kwargs["active_window_end"] = _parse_time_opt(
                data["active_window_end"]
            )
    if "timezone" in data and data["timezone"] is not None:
        kwargs["timezone"] = _parse_timezone(data["timezone"])
    if "days_of_week" in data and data["days_of_week"] is not None:
        kwargs["days_of_week"] = _parse_days(data["days_of_week"])
    if "cooldown_seconds" in data and data["cooldown_seconds"] is not None:
        cd = _nonneg_int(data["cooldown_seconds"], "cooldown_seconds")
        if cd > MAX_COOLDOWN:
            raise RuleValidationError(
                f"cooldown_seconds cannot exceed {MAX_COOLDOWN}."
            )
        kwargs["cooldown_seconds"] = cd
    if "severity" in data and data["severity"] is not None:
        kwargs["severity"] = _parse_severity(data["severity"])
    if "metadata" in data and data["metadata"] is not None:
        kwargs["metadata"] = _parse_metadata(
            data["metadata"], max_bytes=max_metadata_bytes
        )
    _ = current_type
    return AlertRuleUpdate(**kwargs)


def _validate_type_fields(
    rule_type: AlertRuleType,
    *,
    source_event_types: list[str],
    occupancy_threshold: int | None,
    occupancy_duration_seconds: int | None,
    dwell_threshold_seconds: int | None,
    zone_ids: list[str],
) -> None:
    if rule_type is AlertRuleType.EVENT_MATCH:
        if not source_event_types:
            raise RuleValidationError(
                "event_match rules require non-empty source_event_types."
            )
        if occupancy_threshold is not None:
            raise RuleValidationError(
                "event_match rules must not set occupancy_threshold."
            )
        if occupancy_duration_seconds is not None:
            raise RuleValidationError(
                "event_match rules must not set occupancy_duration_seconds."
            )
        if dwell_threshold_seconds is not None:
            raise RuleValidationError(
                "event_match rules must not set dwell_threshold_seconds."
            )
    elif rule_type is AlertRuleType.OCCUPANCY_THRESHOLD:
        if occupancy_threshold is None:
            raise RuleValidationError(
                "occupancy_threshold rules require occupancy_threshold."
            )
        if not zone_ids:
            raise RuleValidationError(
                "occupancy_threshold rules require zone_ids."
            )
        if dwell_threshold_seconds is not None:
            raise RuleValidationError(
                "occupancy_threshold rules must not set dwell_threshold_seconds."
            )
        if source_event_types and set(source_event_types) - {
            "zone_occupancy_changed",
            "zone_entered",
            "zone_exited",
        }:
            raise RuleValidationError(
                "occupancy_threshold source_event_types must be zone-related."
            )
    elif rule_type is AlertRuleType.DWELL_THRESHOLD:
        if dwell_threshold_seconds is None:
            raise RuleValidationError(
                "dwell_threshold rules require dwell_threshold_seconds."
            )
        if not zone_ids:
            raise RuleValidationError(
                "dwell_threshold rules require zone_ids."
            )
        if occupancy_threshold is not None:
            raise RuleValidationError(
                "dwell_threshold rules must not set occupancy_threshold."
            )
        if occupancy_duration_seconds is not None:
            raise RuleValidationError(
                "dwell_threshold rules must not set occupancy_duration_seconds."
            )
        if source_event_types and set(source_event_types) - {
            "zone_entered",
            "zone_exited",
        }:
            raise RuleValidationError(
                "dwell_threshold source_event_types must be zone_entered/zone_exited."
            )


def _require_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuleValidationError("name is required.")
    if len(text) > MAX_NAME:
        raise RuleValidationError(f"name cannot exceed {MAX_NAME} characters.")
    return text


def _parse_rule_type(value: Any) -> AlertRuleType:
    text = str(value or "").strip().lower()
    try:
        return AlertRuleType(text)
    except ValueError as error:
        raise RuleValidationError(
            "rule_type must be one of: event_match, occupancy_threshold, dwell_threshold."
        ) from error


def _parse_severity(value: Any) -> AlertSeverity:
    text = str(value or "").strip().lower()
    try:
        return AlertSeverity(text)
    except ValueError as error:
        raise RuleValidationError(
            "severity must be one of: info, warning, critical."
        ) from error


def _parse_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuleValidationError(f"{field} must be an array.")
    if len(value) > MAX_FILTER_ITEMS:
        raise RuleValidationError(
            f"{field} cannot exceed {MAX_FILTER_ITEMS} entries."
        )
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            raise RuleValidationError(f"{field} entries must be non-empty.")
        if field == "source_event_types":
            if text not in SUPPORTED_SOURCE_EVENT_TYPES:
                raise RuleValidationError(
                    f"unsupported source event type: {text!r}."
                )
            if text not in ALL_TIMELINE_EVENT_TYPES and False:
                pass
        key = text.lower() if field != "zone_ids" else text
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _parse_time_opt(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    match = _TIME_RE.fullmatch(text)
    if not match:
        raise RuleValidationError(
            "active window times must be HH:MM in 24-hour format."
        )
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _parse_timezone(value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise RuleValidationError("timezone is required.")
    try:
        ZoneInfo(text)
    except ZoneInfoNotFoundError as error:
        raise RuleValidationError(
            f"timezone is not a valid IANA identifier: {text!r}."
        ) from error
    return text


def _parse_days(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuleValidationError("days_of_week must be an array of integers 0-6.")
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        day = int(item)
        if day < 0 or day > 6:
            raise RuleValidationError(
                "days_of_week entries must be 0 (Mon) through 6 (Sun)."
            )
        if day not in seen:
            seen.add(day)
            out.append(day)
    return sorted(out)


def _positive_int(value: Any, field: str) -> int:
    number = int(value)
    if number < 1:
        raise RuleValidationError(f"{field} must be an integer >= 1.")
    return number


def _nonneg_int(value: Any, field: str) -> int:
    number = int(value)
    if number < 0:
        raise RuleValidationError(f"{field} must be an integer >= 0.")
    return number


def _parse_metadata(value: Any, *, max_bytes: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleValidationError("metadata must be a JSON object.")
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError) as error:
        raise RuleValidationError(
            "metadata must be JSON-serialisable."
        ) from error
    if len(encoded.encode("utf-8")) > max_bytes:
        raise RuleValidationError(
            f"metadata cannot exceed {max_bytes} bytes."
        )
    return dict(value)
