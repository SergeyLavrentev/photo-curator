from pathlib import Path
from types import SimpleNamespace

from photo_curator.paths import default_application_paths
from photo_curator.photos.doctor import run_doctor
from photo_curator.photos.fake_provider import FakePhotosProvider
from photo_curator.photos.osxphotos_provider import OSXPhotosProvider, _optional_text
from photo_curator.photos.provider import PhotoAsset
from photo_curator.photos.render_resolver import resolve_source_render


def test_fake_provider_passes_doctor_metadata_and_render_read_gate(tmp_path: Path) -> None:
    paths = default_application_paths(tmp_path)
    provider = FakePhotosProvider(paths.cache_dir / "sources")

    checks = {check.code: check for check in run_doctor(provider, paths)}

    assert checks["library"].status == "OK"
    assert checks["albums"].status == "OK"
    assert checks["shared_albums"].status == "WARNING"
    assert checks["read_gate_assets"].status == "OK"
    assert checks["read_gate_render"].status == "OK"


def test_real_provider_contains_single_asset_metadata_failure() -> None:
    class BrokenPhoto:
        uuid = "broken-uuid"
        filename = "broken.jpg"

        @property
        def original_filename(self):
            raise ValueError("unsupported optional property")

    good = SimpleNamespace(
        uuid="good-uuid",
        original_filename="good.jpg",
        filename="good.jpg",
        path_derivatives=[],
        score=None,
    )
    album = SimpleNamespace(uuid="album", photos=[good, BrokenPhoto()])
    provider = OSXPhotosProvider()
    provider._OSXPhotosProvider__db = SimpleNamespace(album_info=[album])

    assets = provider.list_assets("album")

    assert [asset.uuid for asset in assets] == ["good-uuid", "broken-uuid"]
    broken = assets[1]
    assert broken.is_missing is True
    assert broken.provider_error == "ValueError"


def test_osxphotos_empty_burst_sentinels_are_not_persisted_as_real_keys() -> None:
    assert [_optional_text(value) for value in (None, False, 0, "", "0")] == [None] * 5
    assert _optional_text("burst-42") == "burst-42"


def test_review_render_preserves_photokit_degraded_fallback_warning(tmp_path: Path) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"jpeg")
    warning = "Используется локальный preview PhotoKit: iCloud недоступен"

    render = resolve_source_render(
        PhotoAsset(uuid="asset-1", source_path=preview, provider_error=warning)
    )

    assert render.path == preview
    assert render.kind == "original"
    assert render.warning == warning
