from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from photo_curator.logging_setup import MAX_LOG_BYTES, configure_logging


def test_logging_uses_bounded_rotation(tmp_path: Path) -> None:
    log_file = tmp_path / "photo-curator.log"

    configure_logging(log_file)

    rotating = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler)
    ]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == MAX_LOG_BYTES == 5 * 1024 * 1024
    assert rotating[0].backupCount == 2
