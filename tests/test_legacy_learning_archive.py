import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from tests.test_learning import _worker


def legacy_fixture(tmp_path):
    worker, project_id = _worker(tmp_path)
    with database_connection(worker.paths.database) as connection:
        connection.execute("DELETE FROM album_snapshots WHERE project_id=?", (project_id,))
        repository.set_quality_label(
            connection,
            project_id,
            "demo-001",
            disposition="keep",
            defect_codes=[],
            defect_severity=None,
            defect_confidence=None,
            note="Legacy human judgment",
        )
    return worker, project_id


def test_missing_snapshot_project_deletes_only_after_verified_legacy_archive(tmp_path):
    worker, project_id = legacy_fixture(tmp_path)
    with database_connection(worker.paths.database) as connection:
        expected = dict(
            connection.execute(
                "SELECT * FROM quality_asset_labels WHERE project_id=?", (project_id,)
            ).fetchone()
        )
    result = worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})
    archive = result["learning_migration"]
    assert archive["status"] == "archived_not_trainable"
    assert "архив" in result["notice"]
    manifest = json.loads(Path(archive["manifest_path"]).read_text())
    assert manifest["files"]
    for item in manifest["files"]:
        copied = Path(item["archive"])
        assert copied.is_file()
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == item["sha256"]
        assert not Path(item["source"]).exists()
    assert "legacy-learning-archives" in archive["backup_path"]
    with sqlite3.connect(archive["backup_path"]) as backup:
        backup.row_factory = sqlite3.Row
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            dict(
                backup.execute(
                    "SELECT * FROM quality_asset_labels WHERE project_id=?", (project_id,)
                ).fetchone()
            )
            == expected
        )
        assert backup.execute("SELECT COUNT(*) FROM album_snapshots").fetchone()[0] == 0
    with database_connection(worker.paths.database) as connection:
        with pytest.raises(KeyError):
            repository.get_project(connection, project_id)
        assert connection.execute("SELECT COUNT(*) FROM learning_assets").fetchone()[0] == 0
        assert repository.list_preference_examples(connection) == []


@pytest.mark.parametrize("failure_point", ["create_database_backup", "shutil.copy2"])
def test_archive_failure_preserves_project_labels_and_artifacts(
    tmp_path, monkeypatch, failure_point
):
    worker, project_id = legacy_fixture(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("archive unavailable")

    monkeypatch.setattr("photo_curator.legacy_learning_archive." + failure_point, fail)
    with pytest.raises(OSError, match="archive unavailable"):
        worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})
    with database_connection(worker.paths.database) as connection:
        assert repository.get_project(connection, project_id)["id"] == project_id
        asset = repository.get_asset(connection, project_id, "demo-001")
        assert Path(asset["review_path"]).is_file()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM quality_asset_labels WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            == 1
        )


def test_legacy_archive_never_follows_artifact_symlinks(tmp_path):
    worker, project_id = legacy_fixture(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("unrelated")
    root = worker.paths.project_artifacts_dir / project_id
    (root / "outside-link").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink"):
        worker.dispatch("delete_project", {"project_id": project_id, "confirmed": True})
    assert outside.read_text() == "unrelated"
    with database_connection(worker.paths.database) as connection:
        assert repository.get_project(connection, project_id)
