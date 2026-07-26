"""JSON Lines event persistence for Jarvis Edge AI."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from core.events import JarvisEvent


class JsonlEventLogger:
    """Append Jarvis events to a JSON Lines file."""

    def __init__(
        self,
        path: str | Path = "data/events/jarvis-events.jsonl",
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def handle(self, event: JarvisEvent) -> None:
        """Persist one event."""

        encoded = json.dumps(
            event.to_dict(),
            separators=(",", ":"),
            ensure_ascii=False,
        )

        with self._lock:
            with self.path.open("a", encoding="utf-8") as event_file:
                event_file.write(encoded + "\n")
