from __future__ import annotations

from photo_curator.analysis.decision_engine import decide_asset
from photo_curator.analysis.swipe_score import apple_score_percentiles, calculate_swipe_score


def _asset(uuid: str, **values: object) -> dict[str, object]:
    return {
        "asset_uuid": uuid,
        "cache_state": "ready",
        "favorite": False,
        "has_adjustments": False,
        "technical_quality": 0.8,
        "sharpness_percentile": 0.8,
        "contrast_percentile": 0.8,
        "luma_mean": 0.5,
        **values,
    }


def _signals(score: float, *, utility: bool = False) -> dict[str, dict[str, object]]:
    return {
        "aesthetics": {
            "status": "ready",
            "engine_name": "apple-vision-native",
            "engine_version": "1",
            "request_revision": 1,
            "value": {"overall_score": score, "is_utility": utility, "revision": 1},
        },
        "feature_print": {
            "status": "ready",
            "engine_name": "apple-vision-native",
            "engine_version": "1",
            "request_revision": 2,
            "value": {"element_count": 768, "revision": 2},
        },
        "attention_saliency": {
            "status": "ready",
            "engine_name": "apple-vision-native",
            "engine_version": "1",
            "request_revision": 2,
            "value": {"salient_objects": [{"x": 0.2}], "revision": 2},
        },
    }


def test_native_aesthetics_outranks_technical_perfection() -> None:
    visually_strong = calculate_swipe_score(
        _asset("strong", sharpness_percentile=0.05), None, _signals(0.85)
    )
    technically_clean_but_weak = calculate_swipe_score(
        _asset("weak", technical_quality=1.0, sharpness_percentile=1.0),
        None,
        _signals(-0.75),
    )

    assert visually_strong.score > technically_clean_but_weak.score
    assert visually_strong.components["technical_penalty"] == 12
    assert visually_strong.components["generic_aesthetics"] == 92.5
    assert visually_strong.model_versions["aesthetics"].endswith("revision-1")


def test_utility_and_series_are_explicit_non_technical_signals() -> None:
    normal = calculate_swipe_score(_asset("normal"), None, _signals(0.5))
    utility = calculate_swipe_score(_asset("utility"), None, _signals(0.5, utility=True))
    leader = calculate_swipe_score(
        _asset("leader"),
        {"is_leader": True, "kind": "near", "confidence": 0.95},
        _signals(0.5),
    )
    loser = calculate_swipe_score(
        _asset("loser"),
        {
            "is_leader": False,
            "kind": "near",
            "confidence": 0.95,
            "quality_margin": 0.2,
        },
        _signals(0.5),
    )

    assert normal.score - utility.score == 8
    assert leader.score > loser.score
    assert leader.components["best_in_series"] == 100
    assert any(reason["code"] == "best_in_series" for reason in leader.reasons)


def test_missing_aesthetics_is_neutral_and_lowers_confidence() -> None:
    native = calculate_swipe_score(_asset("native"), None, _signals(0.2))
    fallback = calculate_swipe_score(_asset("fallback"), None, {})

    assert fallback.generic_score == 52
    assert fallback.components["generic_aesthetics"] == 50
    assert fallback.confidence < native.confidence
    assert fallback.personal_delta == 0


def test_rich_apple_scores_are_relative_and_zero_means_missing_positive_signal() -> None:
    assets = [
        _asset(
            "first",
            apple_scores={"interesting_subject": 0.9, "pleasant_composition": 0.8},
        ),
        _asset(
            "second",
            apple_scores={"interesting_subject": 0.2, "pleasant_composition": 0.0},
        ),
    ]

    ranks = apple_score_percentiles(assets)

    assert ranks["first"]["interesting_subject"] == 1.0
    assert ranks["second"]["interesting_subject"] == 0.0
    assert "pleasant_composition" not in ranks["second"]


def test_decision_engine_uses_swipe_score_contract_when_provided() -> None:
    result = calculate_swipe_score(_asset("photo"), None, _signals(0.9))
    decision = decide_asset(_asset("photo"), None, "balanced", result)

    assert decision.score == result.score
    assert decision.disposition == "keep"
    assert decision.reasons[0]["code"] == "selection_score"
    assert decision.reasons[1]["code"] == "swipe_score"
    assert decision.components["generic_aesthetics"] == 95
