from __future__ import annotations

import numpy as np
from PIL import Image

TECHNICAL_ENGINE_VERSION = "4-architecture-aware-horizon"


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
    horizon_stride = max(1, int(np.ceil(max(gradient.shape) / 320.0)))
    horizon_angle, horizon_support = _dominant_horizon(
        gx_aligned[::horizon_stride, ::horizon_stride],
        gy_aligned[::horizon_stride, ::horizon_stride],
        gradient[::horizon_stride, ::horizon_stride],
    )
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


def _dominant_horizon(
    gx: np.ndarray, gy: np.ndarray, magnitude: np.ndarray
) -> tuple[float | None, float]:
    """Estimate one long horizon while discounting architectural edge fields."""
    height = magnitude.shape[0]
    top, bottom = int(height * 0.2), max(int(height * 0.8), 1)
    central_magnitude = magnitude[top:bottom]
    if central_magnitude.size < 64:
        return None, 0.0
    nonzero_magnitude = central_magnitude[central_magnitude > 1e-6]
    if nonzero_magnitude.size < 32:
        return None, 0.0
    threshold = max(0.025, float(np.percentile(nonzero_magnitude, 75)))
    central_gx = gx[top:bottom]
    central_gy = gy[top:bottom]
    strong = central_magnitude >= threshold
    # Rasterized diagonal lines contain alternating horizontal and vertical steps,
    # so their per-pixel gradient direction is not a reliable line angle. Keep
    # horizontal-facing edge pixels, then vote across the complete angle range.
    mask = strong & (np.abs(central_gy) >= np.abs(central_gx) * 0.5)
    y, x = np.where(mask)
    if x.size < 32:
        return None, 0.0
    weights = central_magnitude[mask]
    center_x = (magnitude.shape[1] - 1) / 2.0
    intercept_size = max(2.0, height / 80.0)
    intercept_count = max(1, int(np.ceil(height / intercept_size)) + 1)
    candidate_angles = np.arange(-29.0, 30.0, 2.0)
    vote_rows = []
    for candidate_angle in candidate_angles:
        candidate_slope = np.tan(np.radians(candidate_angle))
        intercepts = y + top - candidate_slope * (x - center_x)
        intercept_bin = np.floor(intercepts / intercept_size).astype(np.int32)
        valid = (intercept_bin >= 0) & (intercept_bin < intercept_count)
        vote_rows.append(
            np.bincount(intercept_bin[valid], weights=weights[valid], minlength=intercept_count)
        )
    votes = np.asarray(vote_rows)
    winner = int(np.argmax(votes))
    winner_angle = winner // intercept_count
    winner_intercept = winner % intercept_count
    angle = float(candidate_angles[winner_angle])
    slope = float(np.tan(np.radians(angle)))
    intercept = (winner_intercept + 0.5) * intercept_size
    predicted = intercept + slope * (x - center_x)
    inlier = np.abs((y + top) - predicted) <= intercept_size
    if int(np.count_nonzero(inlier)) < 24:
        return None, 0.0

    x_inlier = x[inlier]
    width = magnitude.shape[1]
    x_bins = np.clip((x_inlier * 16 // max(1, width)).astype(np.int32), 0, 15)
    coverage = len(np.unique(x_bins)) / 16.0
    horizontal_weight = float(np.sum(weights))
    dominance = float(votes.flat[winner]) / max(horizontal_weight, 1e-9)

    # Repeated floor/window/brick lines and strong orthogonal structure are common
    # architectural false positives. They reduce confidence, without claiming a
    # semantic sky/ground detector that this local metric cannot provide.
    same_angle_votes = votes[winner_angle]
    strong_intercepts = np.where(same_angle_votes >= votes.flat[winner] * 0.35)[0]
    parallel_lines = 0
    previous = -10
    for value in strong_intercepts:
        if int(value) - previous > 2:
            parallel_lines += 1
        previous = int(value)
    vertical_weight = float(
        np.sum(central_magnitude[strong & (np.abs(central_gx) > np.abs(central_gy) * 1.5)])
    )
    orthogonal_ratio = vertical_weight / max(horizontal_weight + vertical_weight, 1e-9)
    architecture_discount = 1.0 / (1.0 + 0.65 * max(0, parallel_lines - 1))
    architecture_discount *= max(0.25, 1.0 - orthogonal_ratio)
    support = coverage * min(1.0, dominance * 2.5) * architecture_discount
    angle = float(np.degrees(np.arctan(slope)))
    if abs(angle) > 30.0:
        return None, round(support, 4)
    return (round(angle, 2), round(support, 4)) if support >= 0.08 else (None, round(support, 4))
