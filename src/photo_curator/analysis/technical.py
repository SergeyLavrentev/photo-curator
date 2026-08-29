from __future__ import annotations

import numpy as np
from PIL import Image

TECHNICAL_ENGINE_VERSION = "3-horizon-subject-boundary"


def technical_metrics(image: Image.Image) -> dict[str, float]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    gx = np.diff(luma, axis=1)
    gy = np.diff(luma, axis=0)
    laplacian = (
        -4 * luma[1:-1, 1:-1] + luma[:-2, 1:-1] + luma[2:, 1:-1] + luma[1:-1, :-2] + luma[1:-1, 2:]
    )
    gradient = np.sqrt(gx[:-1, :] ** 2 + gy[:, :-1] ** 2)
    gx_aligned = gx[:-1, :]
    gy_aligned = gy[:, :-1]
    xx = float(np.mean(gx_aligned**2))
    yy = float(np.mean(gy_aligned**2))
    xy = float(np.mean(gx_aligned * gy_aligned))
    trace = xx + yy
    determinant_term = max(0.0, (xx - yy) ** 2 + 4.0 * xy**2) ** 0.5
    directional_coherence = determinant_term / trace if trace > 1e-12 else 0.0
    percentiles = np.percentile(luma, [1, 5, 50, 95, 99])
    histogram, _ = np.histogram(luma, bins=256, range=(0, 1), density=False)
    probabilities = histogram.astype(np.float64) / max(1, histogram.sum())
    nonzero = probabilities[probabilities > 0]
    entropy = float(-(nonzero * np.log2(nonzero)).sum())
    horizon_angle, horizon_support = _dominant_horizon(gradient)
    return {
        "laplacian_variance": float(np.var(laplacian)),
        "gradient_energy": float(np.mean(gradient**2)),
        "edge_density": float(np.mean(gradient > 0.08)),
        "directional_coherence": directional_coherence,
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
        "dominant_horizon_degrees": horizon_angle,
        "horizon_support": horizon_support,
    }


def subject_quality_metrics(
    image: Image.Image,
    rectangles: list[dict[str, object]],
    *,
    source: str,
) -> dict[str, object]:
    """Measure locally important regions reported in Vision's normalized coordinates."""
    width, height = image.size
    regions: list[tuple[Image.Image, float]] = []
    boundary_weight = 0.0
    for rectangle in rectangles[:10]:
        try:
            x = max(0.0, min(1.0, float(rectangle["x"])))
            y = max(0.0, min(1.0, float(rectangle["y"])))
            region_width = max(0.0, min(1.0 - x, float(rectangle["width"])))
            region_height = max(0.0, min(1.0 - y, float(rectangle["height"])))
        except (KeyError, TypeError, ValueError):
            continue
        area = region_width * region_height
        if area < 0.0025:
            continue
        padding_x = region_width * 0.08
        padding_y = region_height * 0.08
        left = max(0, round((x - padding_x) * width))
        right = min(width, round((x + region_width + padding_x) * width))
        # Vision uses a bottom-left origin; PIL uses a top-left origin.
        top = max(0, round((1.0 - y - region_height - padding_y) * height))
        bottom = min(height, round((1.0 - y + padding_y) * height))
        if right - left < 8 or bottom - top < 8:
            continue
        regions.append((image.crop((left, top, right, bottom)), area**0.5))
        contacts = sum(
            (
                x <= 0.012,
                y <= 0.012,
                x + region_width >= 0.988,
                y + region_height >= 0.988,
            )
        )
        if contacts:
            boundary_weight += area**0.5 * min(1.0, contacts / 2.0)
    if not regions:
        return {}
    measured = [(technical_metrics(region), weight) for region, weight in regions]
    total_weight = sum(weight for _, weight in measured)

    def weighted(key: str) -> float:
        return sum(float(values[key]) * weight for values, weight in measured) / total_weight

    return {
        "roi_source": source,
        "region_count": len(measured),
        "subject_coverage": min(1.0, sum(weight**2 for _, weight in measured)),
        "subject_laplacian_variance": weighted("laplacian_variance"),
        "subject_gradient_energy": weighted("gradient_energy"),
        "subject_directional_coherence": weighted("directional_coherence"),
        "subject_luma_p05": weighted("luma_p05"),
        "subject_luma_p95": weighted("luma_p95"),
        "subject_black_clipped_ratio": weighted("black_clipped_ratio"),
        "subject_white_clipped_ratio": weighted("white_clipped_ratio"),
        "subject_boundary_contact_ratio": min(1.0, boundary_weight / total_weight),
        "subject_boundary_source": source,
    }


def _dominant_horizon(magnitude: np.ndarray) -> tuple[float | None, float]:
    """Estimate a supported near-horizontal edge angle; absence remains explicit."""
    height = magnitude.shape[0]
    top, bottom = int(height * 0.2), max(int(height * 0.8), 1)
    central_magnitude = magnitude[top:bottom]
    if central_magnitude.size < 64:
        return None, 0.0
    nonzero_magnitude = central_magnitude[central_magnitude > 1e-6]
    if nonzero_magnitude.size < 32:
        return None, 0.0
    threshold = float(np.percentile(nonzero_magnitude, 70))
    mask = central_magnitude >= threshold
    y, x = np.where(mask)
    weights = central_magnitude[mask]
    if x.size < 32 or int(x.max()) - int(x.min()) < magnitude.shape[1] * 0.4:
        return None, 0.0
    x_mean = float(np.average(x, weights=weights))
    y_mean = float(np.average(y, weights=weights))
    centered_x = x - x_mean
    denominator = float(np.sum(weights * centered_x**2))
    if denominator <= 1e-9:
        return None, 0.0
    slope = float(np.sum(weights * centered_x * (y - y_mean)) / denominator)
    predicted = y_mean + slope * centered_x
    total_variance = float(np.sum(weights * (y - y_mean) ** 2))
    residual = float(np.sum(weights * (y - predicted) ** 2))
    fit = max(0.0, 1.0 - residual / total_variance) if total_variance > 1e-9 else 0.0
    coverage = (int(x.max()) - int(x.min())) / max(1, magnitude.shape[1] - 1)
    support = fit * coverage
    angle = float(np.degrees(np.arctan(slope)))
    if abs(angle) > 30.0:
        return None, round(support, 4)
    return (round(angle, 2), round(support, 4)) if support >= 0.08 else (None, round(support, 4))
