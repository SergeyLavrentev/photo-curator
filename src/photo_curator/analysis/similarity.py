from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps

from photo_curator.analysis.hashes import hamming_distance


def histogram_similarity(left: list[float], right: list[float]) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else 0.0


def normalized_pixel_mae(left: Image.Image, right: Image.Image) -> float:
    size = (256, 256)
    a = np.asarray(left.convert("RGB").resize(size), dtype=np.float32) / 255.0
    b = np.asarray(right.convert("RGB").resize(size), dtype=np.float32) / 255.0
    return float(np.mean(np.abs(a - b)))


def exposure_invariant_crop_mae(left: Image.Image, right: Image.Image) -> float:
    """Compare center content while discounting global exposure and crop aspect changes."""
    size = (256, 256)
    arrays = []
    for image in (left, right):
        fitted = ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
        value = np.asarray(fitted, dtype=np.float32) / 255.0
        mean = np.mean(value, axis=(0, 1), keepdims=True)
        scale = np.std(value, axis=(0, 1), keepdims=True)
        arrays.append((value - mean) / np.maximum(scale, 0.05))
    # Values beyond four standard deviations are irrelevant for this robust distance.
    a, b = (np.clip(value, -4.0, 4.0) for value in arrays)
    return float(min(1.0, np.mean(np.abs(a - b)) / 4.0))


def hash_similarity(left: str, right: str) -> float:
    return 1.0 - hamming_distance(left, right) / 64.0
