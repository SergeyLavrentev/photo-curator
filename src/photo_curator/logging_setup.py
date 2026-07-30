from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_LOG_BYTES = 5 * 1024 * 1024


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_LOG_BYTES,
        backupCount=2,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[file_handler, logging.StreamHandler()],
        force=True,
    )
