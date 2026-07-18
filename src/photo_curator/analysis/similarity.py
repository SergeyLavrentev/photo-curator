from __future__ import annotations

import numpy as np
from PIL import Image

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


def hash_similarity(left: str, right: str) -> float:
    return 1.0 - hamming_distance(left, right) / 64.0
