"""Run the entity query API without vision hardware.

Usage
-----
From the repository root with PYTHONPATH including ``src``:

    PYTHONPATH=src python -m api

Or with uvicorn:

    PYTHONPATH=src uvicorn api.app:create_app_from_config --factory \\
        --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging

import uvicorn

from api.app import build_app_from_loaded_config
from config import load_app_config


def main() -> int:
    app_config = load_app_config()

    logging.basicConfig(
        level=getattr(logging, app_config.logging.level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("jarvis.api")

    if not app_config.api.enabled:
        logger.warning(
            "api.enabled is false; starting anyway because python -m api "
            "was invoked explicitly."
        )

    application = build_app_from_loaded_config(app_config)

    logger.info(
        "Starting entity query API on %s:%s "
        "(activity_stream_enabled=%s)",
        app_config.api.host,
        app_config.api.port,
        getattr(application.state, "activity_stream_enabled", False),
    )

    uvicorn.run(
        application,
        host=app_config.api.host,
        port=app_config.api.port,
        log_level=app_config.logging.level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
