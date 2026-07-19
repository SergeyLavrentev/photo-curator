from pathlib import Path

import pytest

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate
from photo_curator.paths import default_application_paths
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.photos.shared_copy import SharedCopyCoordinator


class FakePhotoKitBridge:
    capability_available = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def add_files(self, **kwargs) -> dict[str, object]:
        self.calls.append(kwargs)
        count = len(kwargs["files"])
        return {
            "album_identifier": kwargs["album_identifier"] or "photokit-album-id",
            "imported": count,
            "reused": 0,
        }


def _coordinator(tmp_path: Path):
    paths = default_application_paths(tmp_path)
    paths.ensure()
    with database_connection(paths.database) as connection:
        migrate(connection)
    provider = FakePhotosProvider(paths.cache_dir / "fixtures")
    bridge = FakePhotoKitBridge()
    coordinator = SharedCopyCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
        bridge=bridge,
    )
    return paths, provider, coordinator, bridge


def test_shared_copy_sample_is_persisted_imported_and_cleaned(tmp_path: Path) -> None:
    paths, _, coordinator, bridge = _coordinator(tmp_path)
    job = coordinator.prepare(
        "demo-shared-album",
        mode="sample",
        sample_size=4,
        destination_album_name="Local Test",
    )

    coordinator.run(str(job["id"]))

    with database_connection(paths.database) as connection:
        result = repository.get_shared_copy_job(connection, str(job["id"]))
    assert result["status"] == "done"
    assert result["processed_items"] == 4
    assert result["imported_items"] == 4
    assert result["reused_items"] == 0
    assert not (paths.cache_dir / "_shared_copies" / str(job["id"])).exists()
    assert bridge.calls[-1]["album_name"] == "Local Test"
    assert len(bridge.calls[-1]["files"]) == 4


def test_shared_copy_rejects_ambiguous_or_unknown_selection(tmp_path: Path) -> None:
    _, _, coordinator, _ = _coordinator(tmp_path)

    with pytest.raises(ValueError, match="Размер выборки"):
        coordinator.prepare("demo-shared-album", mode="sample", sample_size=5)
    with pytest.raises(ValueError, match="отсутствует"):
        coordinator.prepare(
            "demo-shared-album",
            mode="custom",
            asset_uuids=["missing"],
        )


def test_shared_copy_does_not_append_to_an_existing_named_album(tmp_path: Path) -> None:
    _, provider, coordinator, _ = _coordinator(tmp_path)
    existing_name = provider.list_regular_albums()[0].name

    with pytest.raises(ValueError, match="уже существует"):
        coordinator.prepare(
            "demo-shared-album",
            mode="sample",
            sample_size=2,
            destination_album_name=existing_name,
        )


def test_running_shared_copy_is_interrupted_on_restart(tmp_path: Path) -> None:
    paths, _, coordinator, _ = _coordinator(tmp_path)
    job = coordinator.prepare("demo-shared-album", mode="full")
    with database_connection(paths.database) as connection:
        repository.update_shared_copy_job(
            connection,
            str(job["id"]),
            status="running",
            started=True,
        )
        assert repository.mark_running_shared_copies_interrupted(connection) == 1
        interrupted = repository.get_shared_copy_job(connection, str(job["id"]))

    assert interrupted["status"] == "interrupted"


def test_shared_copy_reuses_photokit_album_between_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, coordinator, bridge = _coordinator(tmp_path)
    monkeypatch.setattr("photo_curator.photos.shared_copy.IMPORT_BATCH_SIZE", 2)
    job = coordinator.prepare("demo-shared-album", mode="full")
    coordinator.run(str(job["id"]))

    with database_connection(paths.database) as connection:
        result = repository.get_shared_copy_job(connection, str(job["id"]))
    assert result["status"] == "done"
    assert len(bridge.calls) == 2
    assert bridge.calls[0]["album_identifier"] is None
    assert bridge.calls[1]["album_identifier"] == "photokit-album-id"
