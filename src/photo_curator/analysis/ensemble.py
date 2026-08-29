from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ENSEMBLE_MODEL_VERSION = "pairwise-simplex-v2-album-percentile"
ENSEMBLE_NORMALIZATION = "album-percentile-v1"
ENGINE_KEYS = ("apple", "nima", "mobileclip")


@dataclass(frozen=True, slots=True)
class EnsemblePair:
    album_id: str
    split: str
    preferred: dict[str, float]
    other: dict[str, float]


def fit_ensemble_weights(pairs: list[EnsemblePair]) -> dict[str, object]:
    calibration = [pair for pair in pairs if pair.split == "calibration"]
    held_out = [pair for pair in pairs if pair.split == "held_out"]
    calibration_albums = {pair.album_id for pair in calibration}
    held_out_albums = {pair.album_id for pair in held_out}
    if calibration_albums & held_out_albums:
        raise ValueError("Calibration and held-out albums must be disjoint")
    if len(calibration) < 50 or len(held_out) < 20:
        raise ValueError("Ensemble fitting requires 50 training and 20 held-out pairs")
    if len(calibration_albums) < 2 or not held_out_albums:
        raise ValueError("Ensemble fitting requires independent albums")
    normalized = _album_percentile_pairs(pairs)
    calibration = [pair for pair in normalized if pair.split == "calibration"]
    held_out = [pair for pair in normalized if pair.split == "held_out"]
    matrix = np.asarray([_difference(pair) for pair in calibration], dtype=np.float64)
    logits = np.zeros(len(ENGINE_KEYS), dtype=np.float64)
    for _ in range(1_500):
        weights = _softmax(logits)
        margins = matrix @ weights / 12.0
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(margins, -20.0, 20.0)))
        gradient_weights = matrix.T @ (probabilities - 1.0) / len(matrix) / 12.0
        jacobian = np.diag(weights) - np.outer(weights, weights)
        logits -= 0.15 * (jacobian @ gradient_weights)
    weights = _softmax(logits)
    candidate_accuracy = _accuracy(held_out, weights)
    apple_accuracy = _accuracy(held_out, np.asarray([1.0, 0.0, 0.0]))
    uplift = candidate_accuracy - apple_accuracy
    return {
        "status": "validated" if candidate_accuracy >= 0.65 and uplift >= 0.02 else "failed",
        "model_version": ENSEMBLE_MODEL_VERSION,
        "normalization": ENSEMBLE_NORMALIZATION,
        "weights": {
            key: round(float(weight), 8) for key, weight in zip(ENGINE_KEYS, weights, strict=True)
        },
        "calibration_pairs": len(calibration),
        "held_out_pairs": len(held_out),
        "calibration_album_count": len(calibration_albums),
        "held_out_album_count": len(held_out_albums),
        "held_out_accuracy": round(candidate_accuracy, 6),
        "apple_only_accuracy": round(apple_accuracy, 6),
        "held_out_uplift": round(uplift, 6),
    }


def validated_ensemble_weights(model: object) -> dict[str, float] | None:
    if not isinstance(model, dict) or model.get("status") != "validated":
        return None
    if (
        model.get("model_version") != ENSEMBLE_MODEL_VERSION
        or model.get("normalization") != ENSEMBLE_NORMALIZATION
        or int(model.get("calibration_pairs") or 0) < 50
        or int(model.get("held_out_pairs") or 0) < 20
        or int(model.get("calibration_album_count") or 0) < 2
        or int(model.get("held_out_album_count") or 0) < 1
        or float(model.get("held_out_uplift") or 0.0) < 0.02
    ):
        return None
    raw = model.get("weights")
    if not isinstance(raw, dict):
        return None
    try:
        weights = {key: float(raw[key]) for key in ENGINE_KEYS}
    except (KeyError, TypeError, ValueError):
        return None
    if any(value < 0 or not np.isfinite(value) for value in weights.values()):
        return None
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-4:
        return None
    return weights


def _difference(pair: EnsemblePair) -> list[float]:
    try:
        return [float(pair.preferred[key]) - float(pair.other[key]) for key in ENGINE_KEYS]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Every ensemble pair requires finite engine scores") from error


def _album_percentile_pairs(pairs: list[EnsemblePair]) -> list[EnsemblePair]:
    """Put every engine on the same within-album 0..100 rank scale."""
    values: dict[tuple[str, str], list[float]] = {}
    for pair in pairs:
        for key in ENGINE_KEYS:
            values.setdefault((pair.album_id, key), []).extend(
                (float(pair.preferred[key]), float(pair.other[key]))
            )

    def normalized(album_id: str, key: str, raw: float) -> float:
        album_values = values[(album_id, key)]
        if len(album_values) <= 1:
            return 50.0
        below = sum(value < raw for value in album_values)
        equal = sum(value == raw for value in album_values)
        return (below + (equal - 1) / 2.0) / (len(album_values) - 1) * 100.0

    return [
        EnsemblePair(
            album_id=pair.album_id,
            split=pair.split,
            preferred={
                key: normalized(pair.album_id, key, float(pair.preferred[key]))
                for key in ENGINE_KEYS
            },
            other={
                key: normalized(pair.album_id, key, float(pair.other[key])) for key in ENGINE_KEYS
            },
        )
        for pair in pairs
    ]


def _softmax(values: np.ndarray) -> np.ndarray:
    exponentials = np.exp(values - float(np.max(values)))
    return exponentials / float(np.sum(exponentials))


def _accuracy(pairs: list[EnsemblePair], weights: np.ndarray) -> float:
    correct = sum(float(np.dot(_difference(pair), weights)) > 0 for pair in pairs)
    return correct / len(pairs)
