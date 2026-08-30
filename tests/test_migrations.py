from pathlib import Path

import pytest

from photo_curator.db import migrations as migrations_module
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import MIGRATIONS, SCHEMA_VERSION, migrate


def create_schema_at(connection, version: int) -> None:
    for migration in MIGRATIONS[:version]:
        connection.executescript(migration)
    connection.execute(f"PRAGMA user_version = {version}")
    connection.commit()


def test_initial_migration_creates_all_required_tables(tmp_path: Path) -> None:
    database = tmp_path / "photo-curator.sqlite3"

    with database_connection(database) as connection:
        migrate(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        publish_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(publishes)").fetchall()
        }
        decision_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(decisions)").fetchall()
        }
        quality_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(quality_asset_labels)").fetchall()
        }
        asset_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(assets)").fetchall()
        }

    assert version == SCHEMA_VERSION
    assert {
        "projects",
        "assets",
        "metrics",
        "duplicate_groups",
        "duplicate_members",
        "decisions",
        "jobs",
        "publishes",
        "shared_copy_jobs",
        "analysis_signals",
        "swipe_scores",
        "taste_profiles",
        "preference_examples",
        "taste_rounds",
        "taste_assets",
        "model_registry",
        "quality_asset_labels",
        "quality_preference_examples",
        "album_snapshots",
        "album_snapshot_items",
        "engine_shadow_runs",
        "engine_shadow_nodes",
        "engine_shadow_members",
    } <= tables
    assert "destination_album_id" in publish_columns
    assert {
        "auto_selection",
        "manual_selection",
        "final_selection",
        "manual_rating",
    } <= decision_columns
    assert {
        "defect_codes_json",
        "defect_severity",
        "defect_confidence",
        "quality_note",
        "lab_sampled",
        "expected_disposition",
    } <= quality_columns
    assert {
        "media_type",
        "media_subtypes",
        "creation_timestamp",
        "modification_timestamp",
        "edit_state",
        "source_revision",
    } <= asset_columns


def test_migration_is_idempotent(tmp_path: Path) -> None:
    with database_connection(tmp_path / "db.sqlite3") as connection:
        migrate(connection)
        migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_migration_19_preserves_existing_quality_truth_without_erasing_decisions(
    tmp_path: Path,
) -> None:
    with database_connection(tmp_path / "v18.sqlite3") as connection:
        create_schema_at(connection, 18)
        connection.executescript(
            """
            INSERT INTO projects (
                id, name, library_path, library_fingerprint, album_id, album_name,
                album_full_path, state, created_at, updated_at
            ) VALUES (
                'project', 'Project', '/library', 'fingerprint', 'album', 'Album',
                'Album', 'ready', 'now', 'now'
            );
            INSERT INTO assets (project_id, asset_uuid, created_at, updated_at)
            VALUES ('project', 'asset', 'now', 'now');
            INSERT INTO decisions (
                project_id, asset_uuid, auto_disposition, manual_disposition,
                final_disposition, confidence, manual_override, updated_at
            ) VALUES ('project', 'asset', 'keep', 'reject', 'reject', 1, 1, 'now');
            INSERT INTO quality_asset_labels (
                project_id, asset_uuid, updated_at, lab_sampled
            ) VALUES ('project', 'asset', 'now', 1);
            """
        )

        migrate(connection)

        quality = connection.execute(
            "SELECT expected_disposition FROM quality_asset_labels"
        ).fetchone()[0]
        decision = connection.execute(
            "SELECT manual_disposition, manual_override FROM decisions"
        ).fetchone()

    assert quality == "reject"
    assert tuple(decision) == ("reject", 1)


def test_schema_nine_database_upgrades_without_recreating_project_data(tmp_path: Path) -> None:
    with database_connection(tmp_path / "v9.sqlite3") as connection:
        create_schema_at(connection, 9)
        connection.executescript(
            """
            INSERT INTO projects (
                id, name, library_path, library_fingerprint, album_id, album_name,
                album_full_path, state, created_at, updated_at
            ) VALUES (
                'project', 'Project', '/library', 'fingerprint', 'album', 'Album',
                'Album', 'ready', 'now', 'now'
            );
            """
        )

        migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='quality_asset_labels'"
            ).fetchone()[0]
            == 1
        )
    backups = list((tmp_path / "backups").glob("*-before-schema-v9-to-v21.sqlite3"))
    assert len(backups) == 1
    with database_connection(backups[0]) as backup_connection:
        assert backup_connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert backup_connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_partial_versioned_schema_fails_without_advancing_version(tmp_path: Path) -> None:
    with database_connection(tmp_path / "partial-v9.sqlite3") as connection:
        connection.executescript(
            """
            CREATE TABLE assets (
                project_id TEXT NOT NULL,
                asset_uuid TEXT NOT NULL,
                PRIMARY KEY (project_id, asset_uuid)
            );
            PRAGMA user_version = 9;
            """
        )

        with pytest.raises(RuntimeError, match="missing tables"):
            migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        } == {"assets"}


def test_migration_rolls_back_schema_and_version_when_post_verifier_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "fault-v18.sqlite3"
    with database_connection(database) as connection:
        create_schema_at(connection, 18)
        original_verify = migrations_module.verify_schema

        def fail_post_verification(received_connection, expected_version: int) -> None:
            original_verify(received_connection, expected_version)
            if expected_version == SCHEMA_VERSION:
                raise RuntimeError("injected post-migration failure")

        monkeypatch.setattr(migrations_module, "verify_schema", fail_post_verification)
        with pytest.raises(RuntimeError, match="injected post-migration failure"):
            migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(quality_asset_labels)").fetchall()
        }
        assert "expected_disposition" not in columns

    assert len(list((tmp_path / "backups").glob("*-before-schema-v18-to-v21.sqlite3"))) == 1
