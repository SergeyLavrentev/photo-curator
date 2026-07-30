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
            return CommandResult(args, 0, "photokit-publish\n", "")
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


def test_native_publish_assets_uses_ephemeral_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner()
    importer = make_importer(tmp_path, monkeypatch, runner)

    result = importer.publish_assets("Best", ["asset-1", "asset-2"])

    assert result == {"album_identifier": "album-1", "imported": 2, "reused": 0}
    assert runner.payload == {
        "album_name": "Best",
        "asset_identifiers": ["asset-1", "asset-2"],
    }
    assert [timeout for _, timeout in runner.calls] == [30, 3600]
    assert runner.request_path is not None
    assert not runner.request_path.exists()


def test_native_publish_failure_is_reported_and_request_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner(publish_error="Photos denied access")
    importer = make_importer(tmp_path, monkeypatch, runner)

    with pytest.raises(ValueError, match="Photos denied access"):
        importer.publish("Best", [tmp_path / "one.jpg"])

    assert runner.payload == {
        "album_name": "Best",
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
            return CommandResult(args, 0, "photokit-publish", "")
        return CommandResult(args, 0, "not-json", "")

    importer.runner = invalid_runner

    with pytest.raises(ValueError, match="некорректный результат"):
        importer.publish_assets("Best", ["asset-1"])
