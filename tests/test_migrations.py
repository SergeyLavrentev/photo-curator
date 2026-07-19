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
    } <= tables
    assert "destination_album_id" in publish_columns


def test_migration_is_idempotent(tmp_path: Path) -> None:
    with database_connection(tmp_path / "db.sqlite3") as connection:
        migrate(connection)
        migrate(connection)

        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
