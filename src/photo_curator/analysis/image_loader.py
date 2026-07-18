from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps


def load_normalized(path: Path, max_dimension: int | None = None) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if max_dimension and max(image.size) > max_dimension:
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        return image.copy()
