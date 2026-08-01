from __future__ import annotations

from photo_curator.analysis.decision_engine import album_selection_threshold, decide_asset
from photo_curator.analysis.swipe_score import (
    POSITIVE_APPLE_SCORE_KEYS,
    apple_score_percentiles,
    calculate_swipe_score,
)


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

    assert fallback.generic_score == 52.76
    assert fallback.components["generic_aesthetics"] == 50
    assert fallback.confidence < native.confidence
    assert fallback.personal_delta == 0


def test_missing_detailed_apple_scores_are_neutral_not_repeated_aesthetic_votes() -> None:
    score = calculate_swipe_score(_asset("photo"), None, _signals(0.8))

    assert score.components["generic_aesthetics"] == 90
    assert score.components["content_appeal"] == 50
    assert score.components["moment_and_subject"] == 50


def test_aesthetic_consensus_can_reject_an_absolute_low_outlier() -> None:
    apple = {key: 0.10 for keys in POSITIVE_APPLE_SCORE_KEYS.values() for key in keys}
    photo = _asset("consensus-low")
    score = calculate_swipe_score(
        photo,
        None,
        _signals(-0.8),
        apple_percentiles=apple,
    )

    decision = decide_asset(photo, None, "balanced", score)

    assert score.score <= 25
    assert score.confidence >= 0.82
    assert decision.disposition == "reject"
    assert decision.reasons[0]["code"] == "weak_aesthetics"


def test_reliable_personal_mismatch_can_corroborate_low_generic_aesthetics() -> None:
    photo = _asset("personally-low")
    score = calculate_swipe_score(
        photo,
        None,
        _signals(-0.8),
        personal_delta=-10,
        taste_model_version="test",
        taste_reliability=0.8,
    )

    decision = decide_asset(photo, None, "balanced", score)

    assert decision.disposition == "reject"
    assert any(reason["code"] == "personal_taste_mismatch" for reason in decision.reasons)


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
    assert decision.reasons[0]["code"].startswith("strong_")
    assert any(reason["code"] == "selection_score" for reason in decision.reasons)
    assert any(reason["code"] == "swipe_score" for reason in decision.reasons)
    assert decision.components["generic_aesthetics"] == 95


def test_album_relative_threshold_makes_balanced_selection_meaningfully_strict() -> None:
    scores = list(range(100, 57, -1))

    compact = album_selection_threshold(scores, "compact")
    balanced = album_selection_threshold(scores, "balanced")
    broad = album_selection_threshold(scores, "broad")

    assert compact > balanced > broad
    assert sum(score >= balanced for score in scores) <= 20


def test_low_ranked_photo_is_kept_without_confirmed_duplicate_defect() -> None:
    photo = _asset("soft", sharpness_percentile=0.01)
    score = calculate_swipe_score(photo, None, _signals(0.8))

    decision = decide_asset(
        photo,
        None,
        "balanced",
        score,
        selected_threshold=90,
    )
    visible_codes = {
        reason["code"]
        for reason in decision.reasons
        if reason["code"] not in {"selection_score", "swipe_score"}
    }

    assert decision.disposition == "keep"
    assert "no_confirmed_defect" in visible_codes
    assert "below_album_cutoff" not in visible_codes
