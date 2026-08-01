"""Async PostgreSQL LISTEN worker for the activity stream.

One dedicated connection per API process. Notifications are resolved once
through TimelineService and fanned out via ActivityStreamBroker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.activity_stream import ActivityStreamBroker
from services.timeline_service import TimelineService
from storage.activity_notify import parse_notification_payload
from storage.sqlalchemy_db import _normalise_database_url


class ActivityNotificationListener:
    """LISTEN on a channel and publish resolved timeline events."""

    def __init__(
        self,
        *,
        database_url: str,
        channel: str,
        timeline_service: TimelineService,
        broker: ActivityStreamBroker,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._database_url = _to_psycopg_url(database_url)
        self._channel = channel
        self._timeline = timeline_service
        self._broker = broker
        self._reconnect_initial = max(0.1, float(reconnect_initial_seconds))
        self._reconnect_max = max(
            self._reconnect_initial,
            float(reconnect_max_seconds),
        )
        self._logger = logger or logging.getLogger(__name__)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._ready.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="activity-notification-listener",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ready.clear()

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self) -> None:
        delay = self._reconnect_initial
        while not self._stop.is_set():
            try:
                await self._listen_loop()
                delay = self._reconnect_initial
            except asyncio.CancelledError:
                raise
            except Exception:
                self._ready.clear()
                self._logger.exception(
                    "Activity LISTEN connection failed; retrying in %.1fs",
                    delay,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    delay = min(delay * 2.0, self._reconnect_max)

    async def _listen_loop(self) -> None:
        try:
            import psycopg
            from psycopg import AsyncConnection
        except ImportError as error:  # pragma: no cover
            raise RuntimeError(
                "psycopg is required for the activity stream listener"
            ) from error

        if self._database_url.startswith("sqlite"):
            self._logger.warning(
                "Activity stream listener disabled: SQLite does not support "
                "LISTEN/NOTIFY"
            )
            self._ready.set()
            await self._stop.wait()
            return

        async with await AsyncConnection.connect(
            self._database_url,
            autocommit=True,
        ) as conn:
            await conn.execute(
                psycopg.sql.SQL("LISTEN {}").format(
                    psycopg.sql.Identifier(self._channel)
                )
            )
            self._ready.set()
            self._logger.info(
                "Activity stream LISTEN ready channel=%s",
                self._channel,
            )

            # psycopg3 async notify generator (one connection per API process).
            while not self._stop.is_set():
                try:
                    async for notify in conn.notifies(timeout=1.0):
                        if self._stop.is_set():
                            return
                        await self._handle_notify(notify.payload)
                except TimeoutError:
                    # Idle poll; keep listening until stop is requested.
                    continue

    async def _handle_notify(self, payload: str | bytes | None) -> None:
        try:
            minimal = parse_notification_payload(payload)
        except ValueError:
            self._logger.warning(
                "Ignoring malformed activity notification payload"
            )
            return

        event_id = minimal["event_id"]
        try:
            # Resolve exactly once per notification, then fan-out.
            event = await asyncio.to_thread(
                self._timeline.get_event,
                event_id,
            )
        except Exception:
            self._logger.exception(
                "Failed to resolve timeline event_id=%s",
                event_id,
            )
            return

        try:
            await self._broker.publish(event)
        except Exception:
            self._logger.exception(
                "Failed to fan-out timeline event_id=%s",
                event_id,
            )


def _to_psycopg_url(database_url: str) -> str:
    url = _normalise_database_url(database_url)
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url
