from photo_curator.analysis.ensemble import (
    ENSEMBLE_MODEL_VERSION,
    EnsemblePair,
    fit_ensemble_weights,
    validated_ensemble_weights,
)


def test_pairwise_ensemble_only_activates_after_independent_apple_uplift() -> None:
    pairs = []
    for index in range(60):
        pairs.append(
            EnsemblePair(
                album_id="train-a" if index % 2 else "train-b",
                split="calibration",
                preferred={"apple": 30, "nima": 90, "mobileclip": 80},
                other={"apple": 70, "nima": 30, "mobileclip": 40},
            )
        )
    for _ in range(20):
        pairs.append(
            EnsemblePair(
                album_id="held-out",
                split="held_out",
                preferred={"apple": 35, "nima": 85, "mobileclip": 75},
                other={"apple": 65, "nima": 35, "mobileclip": 45},
            )
        )

    model = fit_ensemble_weights(pairs)
    weights = validated_ensemble_weights(model)

    assert model["status"] == "validated"
    assert model["model_version"] == ENSEMBLE_MODEL_VERSION
    assert model["held_out_uplift"] == 1.0
    assert weights is not None
    assert weights["nima"] > weights["apple"]


def test_unproven_ensemble_is_not_loaded() -> None:
    assert (
        validated_ensemble_weights(
            {
                "status": "validated",
                "model_version": ENSEMBLE_MODEL_VERSION,
                "weights": {"apple": 0.2, "nima": 0.4, "mobileclip": 0.4},
                "calibration_pairs": 50,
                "held_out_pairs": 20,
                "calibration_album_count": 2,
                "held_out_album_count": 1,
                "held_out_uplift": 0.0,
            }
        )
        is None
    )
