from photo_curator.analysis.curation import automatic_selection_state
from photo_curator.analysis.decision_engine import DecisionResult
from photo_curator.analysis.swipe_score import SwipeScoreResult


def _decision(disposition: str = "keep", *flags: str) -> DecisionResult:
    return DecisionResult(disposition, 0.8, 75, {}, list(flags), [])


def _score(value: int) -> SwipeScoreResult:
    return SwipeScoreResult(1, value, float(value), 0.0, 0.7, {}, [], {})


def test_selection_is_independent_from_safe_keep_disposition() -> None:
    assert (
        automatic_selection_state({}, _decision(), None, _score(60), selected_threshold=74)
        == "alternative"
    )
    assert (
        automatic_selection_state({}, _decision(), None, _score(80), selected_threshold=74)
        == "pick"
    )


def test_near_series_loser_is_alternative_even_with_high_global_score() -> None:
    duplicate = {"kind": "near", "is_leader": False}

    assert (
        automatic_selection_state({}, _decision(), duplicate, _score(95), selected_threshold=74)
        == "alternative"
    )


def test_distinct_recommended_series_frame_can_be_an_additional_pick() -> None:
    duplicate = {"kind": "scene", "is_leader": False, "recommended_pick": True}

    assert (
        automatic_selection_state({}, _decision(), duplicate, _score(90), selected_threshold=74)
        == "pick"
    )


def test_reject_and_incomplete_analysis_remain_explicit() -> None:
    assert (
        automatic_selection_state({}, _decision("reject"), None, _score(90), selected_threshold=74)
        == "reject"
    )
    assert (
        automatic_selection_state(
            {}, _decision("keep", "missing_preview"), None, _score(90), selected_threshold=74
        )
        == "review"
    )
    assert (
        automatic_selection_state(
            {}, _decision("keep", "possible_blur"), None, _score(90), selected_threshold=74
        )
        == "pick"
    )


def test_favorite_or_edited_asset_is_picked_but_not_reclassified_for_safety() -> None:
    assert (
        automatic_selection_state(
            {"favorite": True}, _decision(), None, _score(10), selected_threshold=74
        )
        == "pick"
    )
