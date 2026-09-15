"""Preserve unmigratable legacy evidence without inventing training provenance."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from photo_curator.db import repository
from photo_curator.db.connection import create_database_backup
from photo_curator.paths import ApplicationPaths


def archive_legacy_learning(paths: ApplicationPaths, project_id: str) -> dict:
    # A unique directory prevents ordinary rotating backups from expiring this evidence.
    root = paths.data_dir.resolve() / "legacy-learning-archives"
    if root.is_symlink():
        raise ValueError("Legacy archive root must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive = root / str(uuid.uuid4())
    archive.mkdir(mode=0o700)
    backup = create_database_backup(paths.database, archive, reason="legacy-learning")
    with sqlite3.connect(backup) as connection:
        if connection.execute(
            "SELECT COUNT(*) FROM album_snapshots WHERE project_id=?", (project_id,)
        ).fetchone()[0]:
            raise ValueError("Legacy archive fallback requires a missing album snapshot")
        if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise ValueError("Project missing from verified legacy backup")
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            for table in (
                "quality_asset_labels",
                "quality_preference_examples",
                "quality_series_labels",
            )
        }
    files = []
    for category, source_root in (
        ("project-artifacts", paths.project_artifacts_dir),
        ("cache", paths.cache_dir),
    ):
        source_root = source_root.resolve()
        source = source_root / project_id
        if source.resolve().parent != source_root or source.is_symlink():
            raise ValueError("Invalid legacy project artifact path")
        if not source.exists():
            continue
        for original in source.rglob("*"):
            if original.is_symlink() or not original.resolve().is_relative_to(source):
                raise ValueError("Symlink in legacy project artifacts")
            if not original.is_file():
                continue
            destination = archive / category / original.relative_to(source)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            digest = _sha256(original)
            shutil.copy2(original, destination)
            destination.chmod(0o600)
            if _sha256(destination) != digest:
                raise OSError("Legacy artifact copy verification failed")
            files.append({"source": str(original), "archive": str(destination), "sha256": digest})
    manifest = {
        "schema_version": 1,
        "status": "archived_not_trainable",
        "reason": "missing_immutable_album_snapshot",
        "project_id": project_id,
        "created_at": repository.utc_now(),
        "backup_path": str(backup),
        "backup_sha256": _sha256(backup),
        "counts": counts,
        "files": files,
    }
    temporary = archive / "manifest.pending.json"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    if json.loads(temporary.read_text()) != manifest:
        raise OSError("Legacy archive manifest verification failed")
    final = archive / "manifest.json"
    temporary.replace(final)
    # Partial archives are deliberately retained for diagnosis on any failure.
    return {
        "status": manifest["status"],
        "manifest_path": str(final),
        "backup_path": str(backup),
        "counts": counts,
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
