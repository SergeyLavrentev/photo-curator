from pathlib import Path

from photo_curator.paths import default_application_paths


def test_default_paths_follow_macos_conventions() -> None:
    paths = default_application_paths(Path("/Users/tester"))

    assert paths.database == Path(
        "/Users/tester/Library/Application Support/PhotoCurator/photo-curator.sqlite3"
    )
    assert paths.cache_dir == Path("/Users/tester/Library/Caches/PhotoCurator")
    assert paths.project_artifacts_dir == Path(
        "/Users/tester/Library/Application Support/PhotoCurator/project-artifacts"
    )
    assert paths.log_file == Path("/Users/tester/Library/Logs/PhotoCurator/photo-curator.log")


def test_ensure_creates_private_directories(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path)
    paths.ensure()

    assert paths.data_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert paths.log_dir.is_dir()
    assert paths.project_artifacts_dir.is_dir()
