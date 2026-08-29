from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

CALIBRATION_MODEL_VERSION = "decision-platt-v2-model-disagreement"


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    raw_probability: float
    correct: bool
    album_id: str
    split: str


def fit_decision_calibrator(
    observations: list[CalibrationObservation],
) -> dict[str, object]:
    calibration = [row for row in observations if row.split == "calibration"]
    held_out = [row for row in observations if row.split == "held_out"]
    calibration_albums = {row.album_id for row in calibration}
    held_out_albums = {row.album_id for row in held_out}
    if calibration_albums & held_out_albums:
        raise ValueError("Calibration and held-out albums must be disjoint")
    if len(calibration) < 100 or len(held_out) < 30:
        raise ValueError("Calibration requires 100 training and 30 held-out labels")
    if len(calibration_albums) < 2 or not held_out_albums:
        raise ValueError("Calibration requires at least two training albums and one held-out album")
    x = np.asarray([_logit(row.raw_probability) for row in calibration], dtype=np.float64)
    y = np.asarray([float(row.correct) for row in calibration], dtype=np.float64)
    intercept, slope = 0.0, 1.0
    for _ in range(1_000):
        predicted = 1.0 / (1.0 + np.exp(-np.clip(intercept + slope * x, -20.0, 20.0)))
        intercept -= 0.05 * float(np.mean(predicted - y))
        slope -= 0.05 * float(np.mean((predicted - y) * x))
        slope = max(0.05, min(8.0, slope))
    held_out_pairs = [
        (
            apply_decision_calibration(
                row.raw_probability, {"intercept": intercept, "slope": slope}
            ),
            row.correct,
        )
        for row in held_out
    ]
    brier = sum(
        (probability - float(correct)) ** 2 for probability, correct in held_out_pairs
    ) / len(held_out_pairs)
    ece = _ece(held_out_pairs)
    return {
        "status": "validated" if ece <= 0.10 and brier <= 0.20 else "failed",
        "model_version": CALIBRATION_MODEL_VERSION,
        "intercept": round(intercept, 8),
        "slope": round(slope, 8),
        "calibration_samples": len(calibration),
        "held_out_samples": len(held_out),
        "calibration_album_count": len(calibration_albums),
        "held_out_album_count": len(held_out_albums),
        "held_out_brier": round(brier, 6),
        "held_out_ece": round(ece, 6),
    }


def apply_decision_calibration(raw_probability: float, model: object) -> float:
    probability = max(0.001, min(0.999, float(raw_probability)))
    if not isinstance(model, dict):
        return probability
    try:
        intercept = float(model["intercept"])
        slope = float(model["slope"])
    except (KeyError, TypeError, ValueError):
        return probability
    value = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, intercept + slope * _logit(probability)))))
    return max(0.001, min(0.999, value))


def validated_calibration_model(model: object) -> dict[str, object] | None:
    if not isinstance(model, dict):
        return None
    if (
        model.get("status") != "validated"
        or model.get("model_version") != CALIBRATION_MODEL_VERSION
        or int(model.get("calibration_samples") or 0) < 100
        or int(model.get("held_out_samples") or 0) < 30
        or int(model.get("calibration_album_count") or 0) < 2
        or int(model.get("held_out_album_count") or 0) < 1
        or float(model.get("held_out_ece") or 1.0) > 0.10
    ):
        return None
    return model


def _logit(probability: float) -> float:
    bounded = max(0.001, min(0.999, float(probability)))
    return math.log(bounded / (1.0 - bounded))


def _ece(observations: list[tuple[float, bool]]) -> float:
    weighted = 0.0
    for index in range(10):
        low, high = index / 10.0, (index + 1) / 10.0
        bucket = [
            row for row in observations if low <= row[0] < high or (index == 9 and row[0] == 1)
        ]
        if not bucket:
            continue
        probability = sum(row[0] for row in bucket) / len(bucket)
        accuracy = sum(float(row[1]) for row in bucket) / len(bucket)
        weighted += len(bucket) / len(observations) * abs(probability - accuracy)
    return weighted
