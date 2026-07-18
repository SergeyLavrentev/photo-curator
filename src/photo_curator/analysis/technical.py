from __future__ import annotations

import numpy as np
from PIL import Image


def technical_metrics(image: Image.Image) -> dict[str, float]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    gx = np.diff(luma, axis=1)
    gy = np.diff(luma, axis=0)
    laplacian = (
        -4 * luma[1:-1, 1:-1] + luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:]
    )
    gradient = np.sqrt(gx[:-1, :] ** 2 + gy[:, :-1] ** 2)
    percentiles = np.percentile(luma, [1, 5, 50, 95, 99])
    histogram, _ = np.histogram(luma, bins=256, range=(0, 1), density=False)
    probabilities = histogram.astype(np.float64) / max(1, histogram.sum())
    nonzero = probabilities[probabilities > 0]
    entropy = float(-(nonzero * np.log2(nonzero)).sum())
    return {
        "laplacian_variance": float(np.var(laplacian)),
        "gradient_energy": float(np.mean(gradient**2)),
        "edge_density": float(np.mean(gradient > 0.08)),
        "luma_mean": float(np.mean(luma)),
        "luma_std": float(np.std(luma)),
        "luma_p01": float(percentiles[0]),
        "luma_p05": float(percentiles[1]),
        "luma_p50": float(percentiles[2]),
        "luma_p95": float(percentiles[3]),
        "luma_p99": float(percentiles[4]),
        "black_clipped_ratio": float(np.mean(luma <= 0.01)),
        "white_clipped_ratio": float(np.mean(luma >= 0.99)),
        "contrast_std": float(np.std(luma)),
        "dynamic_range": float(percentiles[4] - percentiles[0]),
        "entropy": entropy,
    }
