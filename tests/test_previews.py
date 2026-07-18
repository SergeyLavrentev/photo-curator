from pathlib import Path

from PIL import Image

from photo_curator.pipeline.previews import build_previews, source_fingerprint


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
