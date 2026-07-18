from __future__ import annotations

import numpy as np


def percentile_ranks(values: list[float | None]) -> list[float | None]:
    available = np.asarray([value for value in values if value is not None], dtype=float)
    if not len(available):
        return [None] * len(values)
    if len(available) == 1:
        return [None if value is None else 0.5 for value in values]
    sorted_values = np.sort(available)
    denominator = max(1, len(sorted_values) - 1)
    return [
        None
        if value is None
        else float((np.searchsorted(sorted_values, value, side="right") - 1) / denominator)
        for value in values
    ]


def robust_stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    median = float(np.median(array))
    return {
        "median": median,
        "mad": float(np.median(np.abs(array - median))),
        "p05": float(np.percentile(array, 5)),
        "p95": float(np.percentile(array, 95)),
    }
