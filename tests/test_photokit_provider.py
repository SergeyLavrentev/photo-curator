from __future__ import annotations

import json
from pathlib import Path

from photo_curator.paths import default_application_paths
from photo_curator.photos.photokit_provider import PhotoKitProvider
from photo_curator.utils.subprocesses import CommandResult


def test_photokit_provider_maps_native_albums_assets_and_capability(tmp_path: Path) -> None:
    executable = tmp_path / "photo-curator-photokit"
    executable.touch(mode=0o700)
    calls = []

    def runner(command, *, timeout):
        calls.append((command, timeout))
        if command[-1] == "--capability":
            return CommandResult(command, 0, "photokit-source-v1\n", "")
        if command[1:] == ["albums"]:
            return CommandResult(
                command,
                0,
                json.dumps(
                    {
                        "regular": [
                            {
                                "id": "album/L0/040",
                                "name": "Trip",
                                "is_shared": False,
                                "photo_count": 2,
                                "video_count": 1,
                            }
                        ],
                        "shared": [
                            {
                                "id": "shared/L0/040",
                                "name": "Shared",
                                "is_shared": True,
                                "photo_count": 1,
                                "video_count": 0,
                            }
                        ],
                    }
                ),
                "",
            )
        if command[1] == "album-identifiers":
            return CommandResult(command, 0, json.dumps(["asset/L0/001"]), "")
        output = Path(command[3])
        output.mkdir(parents=True, exist_ok=True)
        render = output / "asset.jpg"
        render.write_bytes(b"jpeg")
        return CommandResult(
            command,
            0,
            json.dumps(
                [
                    {
                        "uuid": "asset/L0/001",
                        "original_filename": "IMG_1.HEIC",
                        "current_filename": "IMG_1.HEIC",
                        "taken_at": "2026-01-01T00:00:00Z",
                        "width": 4032,
                        "height": 3024,
                        "favorite": True,
                        "hidden": False,
                        "is_live_photo": True,
                        "is_burst": False,
                        "burst_key": None,
                        "burst_default_pick": False,
                        "is_missing": False,
                        "is_photo": True,
                        "source_path": str(render),
                        "provider_error": None,
                    }
                ]
            ),
            "",
        )

    provider = PhotoKitProvider(
        default_application_paths(tmp_path), executable=executable, runner=runner
    )

    assert provider.get_current_library().library_path == "photokit://default"
    assert provider.list_regular_albums()[0].id == "album/L0/040"
    assert provider.list_shared_albums()[0].is_shared
    asset = provider.list_assets("album/L0/040")[0]
    assert asset.uuid == "asset/L0/001"
    assert asset.source_path and asset.source_path.is_file()
    assert asset.favorite and asset.is_live_photo
    refreshed = provider.refresh_assets([asset.uuid])
    assert [item.uuid for item in refreshed] == [asset.uuid]
    provider.refresh_library()
    assert provider.asset_still_in_album("album/L0/040", asset.uuid)
    assert any(command[1] == "assets-by-id" for command, _ in calls)
    assert any(command[1] == "album-identifiers" for command, _ in calls)
    assert any(command[-1] == "--capability" for command, _ in calls)


def test_photokit_provider_is_selected_only_for_existing_bundled_helper(
    tmp_path: Path, monkeypatch
) -> None:
    paths = default_application_paths(tmp_path)
    monkeypatch.setenv("PHOTO_CURATOR_PHOTOKIT_HELPER", str(tmp_path / "missing"))
    assert PhotoKitProvider.from_environment(paths) is None

    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setenv("PHOTO_CURATOR_PHOTOKIT_HELPER", str(helper))
    assert PhotoKitProvider.from_environment(paths) is not None
