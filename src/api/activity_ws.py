"""WebSocket endpoint for the real-time activity stream (v0.5.0)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.activity_stream import (
    DEFAULT_LIFECYCLE_TYPES,
    ActivityStreamBroker,
    ActivitySubscription,
)
from storage.timeline_models import ALL_TIMELINE_EVENT_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["activity-stream"])

PROTOCOL_VERSION = "1"
STREAM_VERSION = "0.6.0"

CLOSE_SHUTDOWN = 1001
CLOSE_INTERNAL = 1011
CLOSE_PROTOCOL = 4001
CLOSE_SLOW_CONSUMER = 4002


@router.websocket("/ws/v1/activity")
async def activity_stream(websocket: WebSocket) -> None:
    """Read-only live activity stream (best-effort, non-durable)."""

    app = websocket.app
    config = getattr(app.state, "activity_stream_config", None)
    broker: ActivityStreamBroker | None = getattr(
        app.state,
        "activity_broker",
        None,
    )
    listener_ready = getattr(app.state, "activity_stream_ready", False)

    if config is None or not getattr(config, "enabled", False):
        await websocket.close(code=CLOSE_INTERNAL, reason="activity stream disabled")
        return

    if broker is None or not listener_ready:
        await websocket.close(
            code=CLOSE_INTERNAL,
            reason="activity stream not ready",
        )
        return

    try:
        client = await broker.register()
    except RuntimeError:
        await websocket.close(
            code=CLOSE_PROTOCOL,
            reason="maximum connections reached",
        )
        return

    await websocket.accept()
    heartbeat_interval = float(config.heartbeat_interval_seconds)

    await websocket.send_json(
        {
            "type": "connection.ready",
            "protocol_version": PROTOCOL_VERSION,
            "stream_version": STREAM_VERSION,
            "connected_at": _now_iso(),
            "subscription": {
                "event_types": sorted(DEFAULT_LIFECYCLE_TYPES),
            },
        }
    )

    sender_task = asyncio.create_task(
        _sender_loop(websocket, client),
        name=f"activity-sender-{client.client_id}",
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(client, heartbeat_interval),
        name=f"activity-heartbeat-{client.client_id}",
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(
                    websocket,
                    code="invalid_json",
                    message="Message must be valid JSON.",
                )
                continue

            if not isinstance(message, dict):
                await _send_error(
                    websocket,
                    code="invalid_message",
                    message="Message must be a JSON object.",
                )
                continue

            msg_type = message.get("type")
            if msg_type == "subscription.update":
                try:
                    subscription = _parse_subscription(message.get("filters"))
                except ValueError as error:
                    await _send_error(
                        websocket,
                        code="invalid_subscription",
                        message=str(error),
                    )
                    continue
                updated = await broker.update_subscription(
                    client.client_id,
                    subscription,
                )
                await websocket.send_json(
                    {
                        "type": "subscription.updated",
                        "subscription": updated.to_public_dict(),
                        "updated_at": _now_iso(),
                    }
                )
            elif msg_type == "heartbeat.ack":
                continue
            else:
                await _send_error(
                    websocket,
                    code="unsupported_message",
                    message=f"Unsupported message type: {msg_type!r}.",
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Activity WebSocket failed")
        try:
            await websocket.close(code=CLOSE_INTERNAL, reason="internal error")
        except Exception:
            pass
    finally:
        sender_task.cancel()
        heartbeat_task.cancel()
        await broker.unregister(client.client_id)
        for task in (sender_task, heartbeat_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _sender_loop(websocket: WebSocket, client: Any) -> None:
    try:
        while not client.closed:
            message = await client.queue.get()
            if message.get("type") == "_close":
                code = int(message.get("code", CLOSE_INTERNAL))
                reason = str(message.get("reason", "closed"))
                await websocket.close(code=code, reason=reason[:120])
                return
            await websocket.send_json(message)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Activity sender stopped", exc_info=True)


async def _heartbeat_loop(client: Any, interval: float) -> None:
    try:
        while not client.closed:
            await asyncio.sleep(interval)
            if client.closed:
                return
            try:
                client.queue.put_nowait(
                    {
                        "type": "heartbeat",
                        "sent_at": _now_iso(),
                    }
                )
            except asyncio.QueueFull:
                # Heartbeats are best-effort; do not drop lifecycle events.
                pass
    except asyncio.CancelledError:
        raise


def _parse_subscription(filters: Any) -> ActivitySubscription:
    if filters is None:
        return ActivitySubscription()
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")

    event_types_raw = filters.get("event_types")
    if event_types_raw is None:
        event_types = set(DEFAULT_LIFECYCLE_TYPES)
    else:
        if not isinstance(event_types_raw, list) or not event_types_raw:
            raise ValueError("event_types must be a non-empty array")
        event_types = set()
        for item in event_types_raw:
            text = str(item).strip()
            if text not in ALL_TIMELINE_EVENT_TYPES:
                raise ValueError(f"unsupported event_type: {item!r}")
            event_types.add(text)

    camera_ids = _parse_string_set(filters.get("camera_ids"), "camera_ids")
    entity_types = _parse_string_set(filters.get("entity_types"), "entity_types")
    entity_ids = _parse_uuid_set(filters.get("entity_ids"), "entity_ids")
    zone_ids = _parse_uuid_set(filters.get("zone_ids"), "zone_ids")

    return ActivitySubscription(
        event_types=event_types,
        camera_ids=camera_ids,
        entity_ids=entity_ids,
        entity_types=entity_types,
        zone_ids=zone_ids,
    )


def _parse_string_set(value: Any, field: str) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            raise ValueError(f"{field} entries must be non-empty strings")
        result.add(text)
    return result


def _parse_uuid_set(value: Any, field: str) -> set[UUID]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: set[UUID] = set()
    for item in value:
        try:
            result.add(UUID(str(item)))
        except ValueError as error:
            raise ValueError(f"invalid UUID in {field}: {item!r}") from error
    return result


async def _send_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
) -> None:
    await websocket.send_json(
        {
            "type": "error",
            "code": code,
            "message": message,
            "sent_at": _now_iso(),
        }
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
