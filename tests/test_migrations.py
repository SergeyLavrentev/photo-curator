from pathlib import Path

from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import SCHEMA_VERSION, migrate


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
        "model_registry",
        "quality_asset_labels",
    } <= tables
    assert "destination_album_id" in publish_columns


def test_migration_is_idempotent(tmp_path: Path) -> None:
    with database_connection(tmp_path / "db.sqlite3") as connection:
        migrate(connection)
        migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_schema_nine_database_upgrades_without_recreating_project_data(tmp_path: Path) -> None:
    with database_connection(tmp_path / "v9.sqlite3") as connection:
        connection.executescript(
            """
            CREATE TABLE assets (
                project_id TEXT NOT NULL,
                asset_uuid TEXT NOT NULL,
                PRIMARY KEY (project_id, asset_uuid)
            );
            INSERT INTO assets VALUES ('project', 'asset');
            PRAGMA user_version = 9;
            """
        )

        migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='quality_asset_labels'"
            ).fetchone()[0]
            == 1
        )
