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
            return CommandResult(command, 0, "photokit-source-media-v3\n", "")
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
        if command[1] == "album-photo-identifiers":
            return CommandResult(command, 0, json.dumps(["asset/L0/001"]), "")
        output = Path(command[3])
        output.mkdir(parents=True, exist_ok=True)
        render = output / (
            "asset-repaired.jpg" if command[1] == "repair-assets-by-id" else "asset.jpg"
        )
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
                        "creation_timestamp": 1767225600.125,
                        "modification_timestamp": 1767225601.5,
                        "width": 4032,
                        "height": 3024,
                        "orientation": 1,
                        "favorite": True,
                        "hidden": False,
                        "has_adjustments": True,
                        "is_live_photo": True,
                        "is_burst": False,
                        "burst_key": None,
                        "burst_default_pick": False,
                        "is_missing": False,
                        "is_photo": True,
                        "media_type": "image",
                        "media_subtypes": 8,
                        "edit_state": "adjusted",
                        "source_revision": "photokit-revision-1",
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
    assert asset.has_adjustments and asset.edit_state == "adjusted"
    assert asset.media_type == "image" and asset.media_subtypes == 8
    assert asset.creation_timestamp == 1767225600.125
    assert asset.modification_timestamp == 1767225601.5
    assert asset.source_revision == "photokit-revision-1"
    refreshed = provider.refresh_assets([asset.uuid])
    assert [item.uuid for item in refreshed] == [asset.uuid]
    repaired = provider.repair_assets([asset.uuid])
    assert [item.uuid for item in repaired] == [asset.uuid]
    assert provider.list_assets("album/L0/040")[0].source_path == repaired[0].source_path
    sampled = provider.sample_assets(
        "album/L0/040",
        limit=10,
        excluded_uuids=set(),
    )
    assert [item.uuid for item in sampled] == [asset.uuid]
    provider.refresh_library()
    assert provider.asset_still_in_album("album/L0/040", asset.uuid)
    assert any(command[1] == "assets-by-id" for command, _ in calls)
    assert any(command[1] == "repair-assets-by-id" for command, _ in calls)
    assert any(command[1] == "album-identifiers" for command, _ in calls)
    assert any(command[1] == "album-photo-identifiers" for command, _ in calls)
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


def test_photokit_provider_streams_per_asset_render_progress(tmp_path: Path) -> None:
    executable = tmp_path / "photo-curator-photokit"
    executable.touch(mode=0o700)

    def runner(command, *, timeout):
        del timeout
        if command[1:] == ["albums"]:
            return CommandResult(
                command,
                0,
                json.dumps(
                    {
                        "regular": [
                            {
                                "id": "album-1",
                                "name": "Trip",
                                "photo_count": 2,
                                "video_count": 0,
                            }
                        ],
                        "shared": [],
                    }
                ),
                "",
            )
        raise AssertionError(command)

    streaming_commands = []

    def streaming_runner(command, *, on_stdout_line, timeout):
        del timeout
        streaming_commands.append(command[1])
        assert command[1] in {"asset-metadata-jsonl", "assets-jsonl"}
        output = Path(command[3]) if command[1] == "assets-jsonl" else None
        if output:
            output.mkdir(parents=True, exist_ok=True)
        items = []
        lines = []
        for index in range(2):
            render = output / f"{index}.jpg" if output else None
            if render:
                render.write_bytes(b"jpeg")
            items.append(
                {
                    "uuid": f"asset-{index}",
                    "is_photo": True,
                    "source_path": str(render) if render else None,
                    "review_render": render is not None,
                }
            )
            lines.append(json.dumps({"type": "progress", "processed": index + 1, "total": 2}))
        lines.append(json.dumps({"type": "result", "assets": items}))
        for line in lines:
            on_stdout_line(line)
        return CommandResult(command, 0, "\n".join(lines), "")

    provider = PhotoKitProvider(
        default_application_paths(tmp_path),
        executable=executable,
        runner=runner,
        streaming_runner=streaming_runner,
    )
    metadata_progress = []
    metadata = provider.list_asset_metadata_with_progress(
        "album-1", lambda processed, total: metadata_progress.append((processed, total))
    )
    assert [asset.uuid for asset in metadata] == ["asset-0", "asset-1"]
    assert all(asset.source_path is None and not asset.review_render for asset in metadata)
    assert metadata_progress == [(1, 2), (2, 2)]

    progress = []

    assets = provider.list_assets_with_progress(
        "album-1", lambda processed, total: progress.append((processed, total))
    )

    assert [asset.uuid for asset in assets] == ["asset-0", "asset-1"]
    assert all(asset.review_render for asset in assets)
    assert progress == [(1, 2), (2, 2)]
    assert streaming_commands == ["asset-metadata-jsonl", "assets-jsonl"]

    for asset in assets:
        assert asset.source_path
        asset.source_path.unlink()
    repaired = provider.list_assets_with_progress("album-1", lambda *_: None)
    assert all(asset.source_path and asset.source_path.is_file() for asset in repaired)
    assert streaming_commands == ["asset-metadata-jsonl", "assets-jsonl", "assets-jsonl"]
