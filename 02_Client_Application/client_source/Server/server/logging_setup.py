"""Consistent rotating-file logging for server and client."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO", filename: str = "server.log") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Avoid duplicate handlers when the GUI is restarted in one process.
    for handler in list(root.handlers):
        if getattr(handler, "_syncanvas", False):
            root.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        log_dir / filename, maxBytes=4 * 1024 * 1024, backupCount=4, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler._syncanvas = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console._syncanvas = True  # type: ignore[attr-defined]
    root.addHandler(console)
