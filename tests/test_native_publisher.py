import json
from pathlib import Path

import pytest

from photo_curator.paths import default_application_paths
from photo_curator.photos.native_publisher import NativePhotosImporter
from photo_curator.utils.subprocesses import CommandResult


class RecordingRunner:
    def __init__(self, *, publish_error: str | None = None) -> None:
        self.calls: list[tuple[list[str], int]] = []
        self.payload: dict[str, object] | None = None
        self.request_path: Path | None = None
        self.publish_error = publish_error

    def __call__(self, args: list[str], *, timeout: int) -> CommandResult:
        self.calls.append((args, timeout))
        if args[-1] == "--capability":
            return CommandResult(args, 0, "photokit-publish-reserved-album-v7\n", "")
        if "--delete-album" in args:
            album_identifier = args[-1]
            return CommandResult(
                args,
                0,
                json.dumps({"album_identifier": album_identifier, "deleted": True}) + "\n",
                "",
            )
        self.request_path = Path(args[-1])
        self.payload = json.loads(self.request_path.read_text(encoding="utf-8"))
        if self.publish_error:
            return CommandResult(args, 1, "", f"detail\n{self.publish_error}\n")
        return CommandResult(
            args,
            0,
            'diagnostic\n{"album_identifier":"album-1","imported":2,"reused":0}\n',
            "",
        )


def make_importer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RecordingRunner
) -> NativePhotosImporter:
    paths = default_application_paths(tmp_path)
    paths.ensure()
    helper = tmp_path / "photo-curator-publish-helper"
    helper.write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("PHOTO_CURATOR_PUBLISH_HELPER", str(helper))
    return NativePhotosImporter(paths, runner=runner)


def test_native_duplicate_assets_uses_ephemeral_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    result = importer.duplicate_assets("Best", ["asset-1", "asset-2"], album_identifier="album-1")

    assert result == {"album_identifier": "album-1", "imported": 2, "reused": 0}
    assert runner.payload == {
        "album_name": "Best",
        "destination_album_identifier": "album-1",
        "duplicate_asset_identifiers": ["asset-1", "asset-2"],
    }
    assert [timeout for _, timeout in runner.calls] == [30, 3600]
    assert runner.request_path is not None
    assert not runner.request_path.exists()


def test_native_add_assets_uses_existing_identifiers_without_export_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    importer.add_assets("Best", ["asset-1", "asset-2"], album_identifier="album-1")

    assert runner.payload == {
        "album_name": "Best",
        "destination_album_identifier": "album-1",
        "existing_asset_identifiers": ["asset-1", "asset-2"],
    }


def test_native_reserve_album_creates_an_explicit_empty_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    result = importer.reserve_album("Best")

    assert result["album_identifier"] == "album-1"
    assert runner.payload == {"album_name": "Best", "reserve_album": True}


def test_native_acceptance_album_cleanup_uses_exact_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    result = importer.delete_album("album-created-by-acceptance")

    assert result == {"album_identifier": "album-created-by-acceptance", "deleted": True}
    assert runner.calls[-1] == (
        [str(importer.executable), "--delete-album", "album-created-by-acceptance"],
        120,
    )


def test_native_publish_failure_is_reported_and_request_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner(publish_error="Photos denied access")
    importer = make_importer(tmp_path, monkeypatch, runner)

    with pytest.raises(ValueError, match="Photos denied access"):
        importer.publish("Best", [tmp_path / "one.jpg"], album_identifier="album-1")

    assert runner.payload == {
        "album_name": "Best",
        "destination_album_identifier": "album-1",
        "files": [str(tmp_path / "one.jpg")],
    }
    assert runner.request_path is not None
    assert not runner.request_path.exists()


def test_native_publish_rejects_invalid_helper_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    def invalid_runner(args: list[str], *, timeout: int) -> CommandResult:
        if args[-1] == "--capability":
            return CommandResult(args, 0, "photokit-publish-reserved-album-v7", "")
        return CommandResult(args, 0, "not-json", "")

    importer.runner = invalid_runner

    with pytest.raises(ValueError, match="некорректный результат"):
        importer.duplicate_assets("Best", ["asset-1"], album_identifier="album-1")


def test_native_publish_streams_preparation_and_commit_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    def streaming_runner(args, *, on_stdout_line, timeout):
        del timeout
        assert args[-2] == "--jsonl"
        frames = [
            {"type": "progress", "phase": "prepare", "processed": 1, "total": 2},
            {"type": "progress", "phase": "prepare", "processed": 2, "total": 2},
            {"type": "progress", "phase": "commit", "processed": 2, "total": 2},
            {
                "type": "result",
                "result": {"album_identifier": "album-1", "imported": 2, "reused": 0},
            },
        ]
        for frame in frames:
            on_stdout_line(json.dumps(frame))
        return CommandResult(args, 0, "\n".join(map(json.dumps, frames)), "")

    importer.streaming_runner = streaming_runner
    progress = []

    result = importer.duplicate_assets(
        "Best",
        ["asset-1", "asset-2"],
        album_identifier="album-1",
        progress=lambda phase, processed, total: progress.append((phase, processed, total)),
    )

    assert result["album_identifier"] == "album-1"
    assert progress == [
        ("prepare", 1, 2),
        ("prepare", 2, 2),
        ("commit", 2, 2),
    ]
