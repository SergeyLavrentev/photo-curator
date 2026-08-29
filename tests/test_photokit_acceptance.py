from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_curator import photokit_acceptance
from photo_curator.paths import default_application_paths


class AcceptanceProvider:
    def __init__(self) -> None:
        self.album_removed = False

    def refresh_library(self) -> None:
        pass

    def list_regular_albums(self):
        albums = [SimpleNamespace(id="source-album", name="Source album")]
        if not self.album_removed:
            albums.append(SimpleNamespace(id="acceptance-album", name="Disposable acceptance"))
        return albums

    def list_shared_albums(self):
        return []

    def asset_still_in_album(self, album_identifier: str, asset_uuid: str) -> bool:
        return album_identifier in {"source-album", "acceptance-album"} and asset_uuid == "asset-1"


class AcceptanceImporter:
    capability_available = True

    def __init__(self, provider: AcceptanceProvider) -> None:
        self.provider = provider
        self.deleted: list[str] = []

    def add_assets(self, album_name: str, asset_uuids: list[str], *, progress):
        assert album_name == "Disposable acceptance"
        assert asset_uuids == ["asset-1"]
        progress("prepare", 1, 1)
        progress("commit", 1, 1)
        return {
            "album_identifier": "acceptance-album",
            "added": 1,
            "imported": 0,
            "reused": 0,
        }

    def delete_album(self, album_identifier: str):
        self.deleted.append(album_identifier)
        self.provider.album_removed = True
        return {"album_identifier": album_identifier, "deleted": True}


def configure_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    provider: AcceptanceProvider,
    importer: AcceptanceImporter,
) -> None:
    monkeypatch.setattr(
        photokit_acceptance.PhotoKitProvider,
        "from_environment",
        lambda _paths: provider,
    )
    monkeypatch.setattr(photokit_acceptance, "NativePhotosImporter", lambda _paths: importer)
    monkeypatch.setattr(
        photokit_acceptance,
        "database_connection",
        lambda _database: nullcontext(object()),
    )
    monkeypatch.setattr(
        photokit_acceptance.repository,
        "get_project",
        lambda _connection, _project_id: {
            "library_path": "photokit://library",
            "album_id": "source-album",
        },
    )
    monkeypatch.setattr(
        photokit_acceptance.repository,
        "list_assets",
        lambda _connection, _project_id: [{"asset_uuid": "asset-1", "no_longer_exists": False}],
    )


def test_photokit_acceptance_removes_only_created_album(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AcceptanceProvider()
    importer = AcceptanceImporter(provider)
    configure_acceptance(monkeypatch, provider, importer)

    result = photokit_acceptance.run_photokit_acceptance(
        default_application_paths(tmp_path),
        project_id="project-1",
        album_name="Disposable acceptance",
    )

    assert result["passed"] is True
    assert result["acceptance_album_removed"] is True
    assert result["source_asset_preserved_after_cleanup"] is True
    assert importer.deleted == ["acceptance-album"]


def test_photokit_acceptance_two_phase_cleanup_is_gui_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AcceptanceProvider()
    importer = AcceptanceImporter(provider)
    configure_acceptance(monkeypatch, provider, importer)
    paths = default_application_paths(tmp_path)

    prepared = photokit_acceptance.prepare_photokit_acceptance(
        paths,
        project_id="project-1",
        album_name="Disposable acceptance",
    )

    assert prepared["album_identifier"] == "acceptance-album"
    assert importer.deleted == []
    provider.album_removed = True

    result = photokit_acceptance.finalize_photokit_acceptance(paths, prepared=prepared)

    assert result["passed"] is True
    assert result["acceptance_album_removed"] is True
    assert result["source_asset_preserved_after_cleanup"] is True


def test_photokit_acceptance_cleans_up_when_membership_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AcceptanceProvider()
    importer = AcceptanceImporter(provider)
    configure_acceptance(monkeypatch, provider, importer)

    def fail_membership(album_identifier: str, _asset_uuid: str) -> bool:
        if album_identifier == "source-album":
            return True
        raise RuntimeError("membership failed")

    provider.asset_still_in_album = fail_membership  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="membership failed"):
        photokit_acceptance.run_photokit_acceptance(
            default_application_paths(tmp_path),
            project_id="project-1",
            album_name="Disposable acceptance",
        )

    assert importer.deleted == ["acceptance-album"]
