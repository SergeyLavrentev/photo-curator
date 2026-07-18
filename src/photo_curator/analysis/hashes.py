from __future__ import annotations

import hashlib

import numpy as np
from PIL import Image


def render_equivalence_hash(image: Image.Image) -> str:
    normalized = image.convert("RGB").resize((256, 256), Image.Resampling.LANCZOS)
    return hashlib.sha256(np.asarray(normalized, dtype=np.uint8).tobytes()).hexdigest()


def dhash(image: Image.Image) -> str:
    grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = np.asarray(grayscale, dtype=np.uint8)
    return _bits_to_hex(pixels[:, 1:] > pixels[:, :-1])


def phash(image: Image.Image) -> str:
    grayscale = image.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    pixels = np.asarray(grayscale, dtype=np.float64)
    dct = _dct_matrix(32) @ pixels @ _dct_matrix(32).T
    low = dct[:8, :8].copy()
    median = float(np.median(low.flatten()[1:]))
    return _bits_to_hex(low > median)


def color_histogram(image: Image.Image, bins: int = 8) -> list[float]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    values = []
    for channel in range(3):
        hist, _ = np.histogram(rgb[:, :, channel], bins=bins, range=(0, 256))
        values.extend(hist.tolist())
    array = np.asarray(values, dtype=np.float64)
    array /= max(1.0, float(array.sum()))
    return array.tolist()


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _dct_matrix(size: int) -> np.ndarray:
    matrix = np.empty((size, size), dtype=np.float64)
    factor = np.pi / (2 * size)
    for k in range(size):
        scale = np.sqrt(1 / size) if k == 0 else np.sqrt(2 / size)
        matrix[k, :] = scale * np.cos((2 * np.arange(size) + 1) * k * factor)
    return matrix


def _bits_to_hex(bits: np.ndarray) -> str:
    flattened = bits.flatten()
    value = 0
    for bit in flattened[:64]:
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"
