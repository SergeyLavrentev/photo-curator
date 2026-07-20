from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Protocol

from photo_curator.utils.identifiers import new_id
from photo_curator.utils.timestamps import utc_now

COREML_SUFFIXES = {".mlmodel", ".mlpackage", ".mlmodelc"}
VALID_STATUSES = {"candidate", "approved", "rejected", "invalid"}


class _Digest(Protocol):
    def update(self, data: bytes, /) -> object: ...


class ModelRegistryError(ValueError):
    """A model does not satisfy the local product trust contract."""


def model_sha256(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if resolved.suffix not in COREML_SUFFIXES or not resolved.exists():
        raise ModelRegistryError("Core ML model must be .mlmodel, .mlpackage or .mlmodelc")
    digest = hashlib.sha256()
    if resolved.is_file():
        _hash_file(digest, resolved)
        return digest.hexdigest()
    files = sorted(item for item in resolved.rglob("*") if item.is_file())
    if not files:
        raise ModelRegistryError("Core ML model directory is empty")
    for item in files:
        if item.is_symlink():
            raise ModelRegistryError("Core ML model must not contain symlinks")
        relative = item.relative_to(resolved).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(item.stat().st_size.to_bytes(8, "big"))
        _hash_file(digest, item)
    return digest.hexdigest()


def register_model(
    connection: sqlite3.Connection,
    *,
    name: str,
    version: str,
    model_path: Path,
    license_id: str,
    source_url: str | None,
    commercial_use_allowed: bool,
) -> dict[str, object]:
    name = name.strip()
    version = version.strip()
    license_id = license_id.strip()
    if not name or not version or not license_id:
        raise ModelRegistryError("Model name, version and license are required")
    path = model_path.expanduser().resolve()
    checksum = model_sha256(path)
    now = utc_now()
    model_id = new_id()
    connection.execute(
        """
        INSERT INTO model_registry (
            id, name, version, model_path, sha256, license_id, source_url,
            commercial_use_allowed, compute_policy, status, compatibility_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'all', 'candidate', '{}', ?, ?)
        ON CONFLICT(name, version) DO UPDATE SET
            model_path=excluded.model_path,
            sha256=excluded.sha256,
            license_id=excluded.license_id,
            source_url=excluded.source_url,
            commercial_use_allowed=excluded.commercial_use_allowed,
            compute_policy='all',
            status='candidate',
            compatibility_json='{}',
            updated_at=excluded.updated_at
        """,
        (
            model_id,
            name,
            version,
            str(path),
            checksum,
            license_id,
            source_url,
            int(commercial_use_allowed),
            now,
            now,
        ),
    )
    return get_model(connection, name=name, version=version)


def get_model(
    connection: sqlite3.Connection,
    *,
    model_id: str | None = None,
    name: str | None = None,
    version: str | None = None,
) -> dict[str, object]:
    if model_id:
        row = connection.execute("SELECT * FROM model_registry WHERE id=?", (model_id,)).fetchone()
    elif name and version:
        row = connection.execute(
            "SELECT * FROM model_registry WHERE name=? AND version=?", (name, version)
        ).fetchone()
    else:
        raise ModelRegistryError("Model id or name and version are required")
    if not row:
        raise KeyError(model_id or f"{name}:{version}")
    return _decode(row)


def list_models(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT * FROM model_registry ORDER BY name, version, created_at"
    ).fetchall()
    return [_decode(row) for row in rows]


def approve_model(
    connection: sqlite3.Connection,
    model_id: str,
    *,
    compatibility: dict[str, object],
) -> dict[str, object]:
    model = get_model(connection, model_id=model_id)
    if not model["commercial_use_allowed"]:
        raise ModelRegistryError("Model license does not allow commercial product use")
    if not compatibility.get("compatible"):
        raise ModelRegistryError("Model compatibility has not passed")
    if model_sha256(Path(str(model["model_path"]))) != model["sha256"]:
        connection.execute(
            "UPDATE model_registry SET status='invalid', updated_at=? WHERE id=?",
            (utc_now(), model_id),
        )
        raise ModelRegistryError("Model checksum changed after registration")
    connection.execute(
        """
        UPDATE model_registry
        SET status='approved', compatibility_json=?, updated_at=?
        WHERE id=?
        """,
        (json.dumps(compatibility, sort_keys=True), utc_now(), model_id),
    )
    return get_model(connection, model_id=model_id)


def revalidate_model(connection: sqlite3.Connection, model_id: str) -> bool:
    model = get_model(connection, model_id=model_id)
    try:
        unchanged = model_sha256(Path(str(model["model_path"]))) == model["sha256"]
    except ModelRegistryError:
        unchanged = False
    if not unchanged:
        connection.execute(
            "UPDATE model_registry SET status='invalid', updated_at=? WHERE id=?",
            (utc_now(), model_id),
        )
    return unchanged


def _decode(row: sqlite3.Row) -> dict[str, object]:
    value = dict(row)
    value["commercial_use_allowed"] = bool(value["commercial_use_allowed"])
    value["compatibility"] = json.loads(str(value.pop("compatibility_json")))
    return value


def _hash_file(digest: _Digest, path: Path) -> None:
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
