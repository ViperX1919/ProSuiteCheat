"""Application logging configuration."""

from __future__ import annotations

import logging
import sys

from .paths import log_path

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def configure(level: int = logging.INFO) -> None:
    """Install file + stream handlers. Safe to call more than once."""
    root = logging.getLogger()
    if root.handlers:
        return

    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(log_path(), encoding="utf-8"))
    except OSError:
        # A read-only install directory should not stop the app from starting.
        pass
    handlers.append(logging.StreamHandler())

    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers)

    if sys.platform != "win32":
        logging.getLogger(__name__).error(
            "Raw mouse input uses Windows-only APIs; this platform is unsupported."
        )
