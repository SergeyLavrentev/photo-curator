from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MIN_ANALYSIS_PREVIEW_SHORT_EDGE = 256
MIN_ANALYSIS_PREVIEW_LONG_EDGE = 512


@dataclass(frozen=True, slots=True)
class PreviewResult:
    review_path: Path
    thumbnail_path: Path
    source_fingerprint: str


def source_fingerprint(path: Path, source_kind: str, settings_version: int = 2) -> str:
    stat = path.stat()
    content = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            content.update(chunk)
    payload = (
        f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{content.hexdigest()}|"
        f"{source_kind}|{settings_version}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def analysis_preview_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def analysis_preview_is_eligible(path: Path) -> bool:
    width, height = analysis_preview_dimensions(path)
    return (
        min(width, height) >= MIN_ANALYSIS_PREVIEW_SHORT_EDGE
        and max(width, height) >= MIN_ANALYSIS_PREVIEW_LONG_EDGE
    )


def build_previews(
    source: Path,
    review_path: Path,
    thumbnail_path: Path,
    *,
    source_kind: str,
    source_is_review_render: bool = False,
    shared_thumbnail_path: Path | None = None,
) -> PreviewResult:
    fingerprint = source_fingerprint(source, source_kind)
    review_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if source_is_review_render and shared_thumbnail_path and shared_thumbnail_path.is_file():
        _atomic_link_or_copy(source, review_path)
        _atomic_link_or_copy(shared_thumbnail_path, thumbnail_path)
        return PreviewResult(review_path, thumbnail_path, fingerprint)
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if source_is_review_render:
                _atomic_link_or_copy(source, review_path)
            else:
                _atomic_jpeg(image, review_path, max_dimension=2048, quality=88)
            if source_is_review_render and shared_thumbnail_path:
                shared_thumbnail_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _atomic_jpeg(image, shared_thumbnail_path, max_dimension=320, quality=75)
                _atomic_link_or_copy(shared_thumbnail_path, thumbnail_path)
            else:
                _atomic_jpeg(image, thumbnail_path, max_dimension=320, quality=75)
    except (OSError, ValueError):
        _sips_convert(source, review_path, 2048)
        with Image.open(review_path) as review:
            _atomic_jpeg(review.convert("RGB"), thumbnail_path, max_dimension=320, quality=75)
    return PreviewResult(review_path, thumbnail_path, fingerprint)


def shared_thumbnail_cache_path(review_render: Path) -> Path:
    return review_render.with_name(f"{review_render.stem}.thumb-v1-320-q75.jpg")


def _atomic_link_or_copy(source: Path, destination: Path) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=".preview-", suffix=".jpg", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_jpeg(
    image: Image.Image, destination: Path, *, max_dimension: int, quality: int
) -> None:
    output = image.copy()
    output.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    fd, temporary_name = tempfile.mkstemp(prefix=".preview-", suffix=".jpg", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        output.save(temporary, "JPEG", quality=quality, optimize=True)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _sips_convert(source: Path, destination: Path, max_dimension: int) -> None:
    temporary = destination.with_name(f".{destination.name}.sips.jpg")
    result = subprocess.run(
        [
            "/usr/bin/sips",
            "-s",
            "format",
            "jpeg",
            "-Z",
            str(max_dimension),
            str(source),
            "--out",
            str(temporary),
        ],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise OSError(f"sips conversion failed: {result.stderr.strip()}")
    temporary.replace(destination)
