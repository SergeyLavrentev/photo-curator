from pathlib import Path

from PIL import Image

from photo_curator.pipeline.previews import (
    analysis_preview_is_eligible,
    build_previews,
    shared_thumbnail_cache_path,
    source_fingerprint,
)


def test_analysis_preview_resolution_gate_is_fail_closed(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny.jpg"
    valid = tmp_path / "valid.jpg"
    Image.new("RGB", (48, 64), "gray").save(tiny)
    Image.new("RGB", (768, 1024), "gray").save(valid)

    assert analysis_preview_is_eligible(tiny) is False
    assert analysis_preview_is_eligible(valid) is True


def test_preview_builder_normalizes_orientation_and_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (80, 120), "red")
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)
    review = tmp_path / "cache" / "review.jpg"
    thumb = tmp_path / "cache" / "thumb.jpg"

    result = build_previews(source, review, thumb, source_kind="original")

    with Image.open(result.review_path) as rendered:
        assert rendered.size == (120, 80)
    with Image.open(result.thumbnail_path) as rendered:
        assert max(rendered.size) <= 320
    assert not list(review.parent.glob(".preview-*"))


def test_source_fingerprint_changes_with_source(tmp_path: Path) -> None:
    source = tmp_path / "image.jpg"
    source.write_bytes(b"first")
    first = source_fingerprint(source, "original")
    source.write_bytes(b"second-version")

    assert source_fingerprint(source, "original") != first


def test_preview_builder_reuses_native_review_render_without_reencoding(tmp_path: Path) -> None:
    source = tmp_path / "photokit-render.jpg"
    Image.new("RGB", (640, 480), "blue").save(source, "JPEG", quality=88)
    source_bytes = source.read_bytes()
    review = tmp_path / "cache" / "review.jpg"
    thumb = tmp_path / "cache" / "thumb.jpg"

    result = build_previews(
        source,
        review,
        thumb,
        source_kind="original",
        source_is_review_render=True,
    )

    assert result.review_path.read_bytes() == source_bytes
    with Image.open(result.thumbnail_path) as rendered:
        assert max(rendered.size) <= 320


def test_preview_builder_reuses_shared_native_thumbnail_cache(tmp_path: Path) -> None:
    source = tmp_path / "photokit-render.jpg"
    Image.new("RGB", (640, 480), "green").save(source, "JPEG", quality=88)
    shared_thumbnail = shared_thumbnail_cache_path(source)
    first_review = tmp_path / "first" / "review.jpg"
    first_thumb = tmp_path / "first" / "thumb.jpg"
    build_previews(
        source,
        first_review,
        first_thumb,
        source_kind="original",
        source_is_review_render=True,
        shared_thumbnail_path=shared_thumbnail,
    )
    cached_bytes = shared_thumbnail.read_bytes()
    cached_mtime = shared_thumbnail.stat().st_mtime_ns

    second_review = tmp_path / "second" / "review.jpg"
    second_thumb = tmp_path / "second" / "thumb.jpg"
    build_previews(
        source,
        second_review,
        second_thumb,
        source_kind="original",
        source_is_review_render=True,
        shared_thumbnail_path=shared_thumbnail,
    )

    assert shared_thumbnail.stat().st_mtime_ns == cached_mtime
    assert second_review.read_bytes() == source.read_bytes()
    assert second_thumb.read_bytes() == cached_bytes
