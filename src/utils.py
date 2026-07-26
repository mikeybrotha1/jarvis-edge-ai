"""Shared utilities for Jarvis Edge AI."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_file: str = "logs/jarvis.log") -> logging.Logger:
    """Configure console and file logging."""

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(path),
        ],
    )

    return logging.getLogger("jarvis")
