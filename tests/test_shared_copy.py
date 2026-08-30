from pathlib import Path

import pytest

from photo_curator.db import repository
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate
from photo_curator.paths import default_application_paths
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.photos.local_provider import LocalAlbumsProvider
from photo_curator.photos.shared_copy import SharedCopyCoordinator


def _coordinator(tmp_path: Path):
    paths = default_application_paths(tmp_path)
    paths.ensure()
    with database_connection(paths.database) as connection:
        migrate(connection)
    base = FakePhotosProvider(paths.cache_dir / "fixtures")
    provider = LocalAlbumsProvider(base, paths.data_dir / "local_albums")
    coordinator = SharedCopyCoordinator(
        database_path=paths.database,
        paths=paths,
        provider=provider,
    )
    return paths, provider, coordinator


def test_shared_copy_sample_is_persisted_and_exposed_as_local_album(tmp_path: Path) -> None:
    paths, provider, coordinator = _coordinator(tmp_path)
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
    album_id = str(result["destination_album_id"])
    albums = {album.id: album for album in provider.list_regular_albums()}
    assert albums[album_id].name == "Local Test"
    assets = provider.list_assets(album_id)
    assert len(assets) == 4
    assert all(asset.source_path and asset.source_path.is_file() for asset in assets)
    assert all(paths.data_dir in asset.source_path.parents for asset in assets if asset.source_path)


def test_shared_copy_rejects_ambiguous_or_unknown_selection(tmp_path: Path) -> None:
    _, _, coordinator = _coordinator(tmp_path)

    with pytest.raises(ValueError, match="Размер выборки"):
        coordinator.prepare("demo-shared-album", mode="sample", sample_size=5)
    with pytest.raises(ValueError, match="отсутствует"):
        coordinator.prepare(
            "demo-shared-album",
            mode="custom",
            asset_uuids=["missing"],
        )


def test_shared_copy_does_not_append_to_an_existing_named_album(tmp_path: Path) -> None:
    _, provider, coordinator = _coordinator(tmp_path)
    existing_name = provider.list_regular_albums()[0].name

    with pytest.raises(ValueError, match="уже существует"):
        coordinator.prepare(
            "demo-shared-album",
            mode="sample",
            sample_size=2,
            destination_album_name=existing_name,
        )


def test_running_shared_copy_is_interrupted_on_restart(tmp_path: Path) -> None:
    paths, _, coordinator = _coordinator(tmp_path)
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


def test_shared_copy_reuses_files_when_resumed(tmp_path: Path) -> None:
    paths, _, coordinator = _coordinator(tmp_path)
    job = coordinator.prepare("demo-shared-album", mode="full")
    coordinator.run(str(job["id"]))
    coordinator.run(str(job["id"]))

    with database_connection(paths.database) as connection:
        result = repository.get_shared_copy_job(connection, str(job["id"]))
    assert result["status"] == "done"
    assert result["imported_items"] == 0
    assert result["reused_items"] == 4


def test_local_provider_forwards_preview_repair_to_photokit_base(tmp_path: Path) -> None:
    _, provider, _ = _coordinator(tmp_path)
    calls: list[list[str]] = []

    def repair_assets(asset_uuids: list[str]):
        calls.append(list(asset_uuids))
        return provider.base.refresh_assets(asset_uuids)

    provider.base.repair_assets = repair_assets

    repaired = provider.repair_assets(["demo-001"])

    assert calls == [["demo-001"]]
    assert [asset.uuid for asset in repaired] == ["demo-001"]
