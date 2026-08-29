from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def create_database_backup(
    database_path: Path,
    backup_dir: Path,
    *,
    reason: str,
    keep: int = 10,
) -> Path:
    """Create a transactionally consistent SQLite backup and rotate old copies."""
    if keep < 1:
        raise ValueError("keep must be positive")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    safe_reason = "".join(char if char.isalnum() or char in "-_" else "-" for char in reason)
    destination = backup_dir / f"photo-curator-{timestamp}-{safe_reason}.sqlite3"
    source = sqlite3.connect(database_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"backup integrity check failed: {result!r}")
    except Exception:
        target.close()
        source.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        target.close()
        source.close()
    destination.chmod(0o600)
    backups = sorted(backup_dir.glob("photo-curator-*.sqlite3"), reverse=True)
    for expired in backups[keep:]:
        expired.unlink(missing_ok=True)
    return destination


@contextmanager
def database_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
