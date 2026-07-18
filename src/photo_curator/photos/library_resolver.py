from __future__ import annotations

import hashlib
from pathlib import Path


def library_fingerprint(
    library_path: str | Path,
    database_path: str | Path | None,
    database_version: str | int | None,
) -> str:
    library = Path(library_path).expanduser()
    database = Path(database_path).expanduser() if database_path else None
    size = database.stat().st_size if database and database.exists() else 0
    mtime = database.stat().st_mtime_ns if database and database.exists() else 0
    payload = f"{library.resolve()}|{database}|{size}|{mtime}|{database_version}"
    return hashlib.sha256(payload.encode()).hexdigest()
