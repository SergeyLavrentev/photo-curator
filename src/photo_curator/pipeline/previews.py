from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


@dataclass(frozen=True, slots=True)
class PreviewResult:
    review_path: Path
    thumbnail_path: Path
    source_fingerprint: str


def source_fingerprint(path: Path, source_kind: str, settings_version: int = 1) -> str:
    stat = path.stat()
    payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{source_kind}|{settings_version}"
    return hashlib.sha256(payload.encode()).hexdigest()


def build_previews(
    source: Path,
    review_path: Path,
    thumbnail_path: Path,
    *,
    source_kind: str,
) -> PreviewResult:
    fingerprint = source_fingerprint(source, source_kind)
    review_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            _atomic_jpeg(image, review_path, max_dimension=2048, quality=88)
            _atomic_jpeg(image, thumbnail_path, max_dimension=320, quality=75)
    except (OSError, ValueError):
        _sips_convert(source, review_path, 2048)
        with Image.open(review_path) as review:
            _atomic_jpeg(review.convert("RGB"), thumbnail_path, max_dimension=320, quality=75)
    return PreviewResult(review_path, thumbnail_path, fingerprint)


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
