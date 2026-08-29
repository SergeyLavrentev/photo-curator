from photo_curator.analysis.calibration import (
    CalibrationObservation,
    apply_decision_calibration,
    fit_decision_calibrator,
    validated_calibration_model,
)


def test_decision_calibration_requires_album_separation_and_passes_held_out_gate() -> None:
    observations = []
    for index in range(120):
        probability = 0.05 + (index % 20) / 21
        observations.append(
            CalibrationObservation(
                probability,
                probability >= 0.5,
                "train-a" if index % 2 else "train-b",
                "calibration",
            )
        )
    for index in range(40):
        probability = 0.05 + (index % 20) / 21
        observations.append(
            CalibrationObservation(probability, probability >= 0.5, "held-out", "held_out")
        )

    model = fit_decision_calibrator(observations)

    assert model["status"] == "validated"
    assert validated_calibration_model(model) == model
    assert apply_decision_calibration(0.9, model) > 0.9
    assert apply_decision_calibration(0.1, model) < 0.1


def test_decision_calibration_rejects_album_leakage() -> None:
    rows = [CalibrationObservation(0.8, True, "same", "calibration") for _ in range(100)] + [
        CalibrationObservation(0.8, True, "same", "held_out") for _ in range(30)
    ]

    try:
        fit_decision_calibrator(rows)
    except ValueError as error:
        assert "disjoint" in str(error)
    else:
        raise AssertionError("Album leakage must be rejected")
